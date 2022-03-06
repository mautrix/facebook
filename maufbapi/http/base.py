# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

from typing import Type, TypeVar
from contextlib import asynccontextmanager
from urllib.parse import quote
import base64
import hashlib
import json
import logging
import pkgutil
import random
import time
import urllib.request

from aiohttp import ClientResponse, ClientSession
from aiohttp.client import _RequestContextManager
from yarl import URL
import aiohttp
import zstandard as zstd

from mautrix.types import JSON
from mautrix.util.logging import TraceLogger

from ..state import AndroidState
from ..types import GraphQLMutation, GraphQLQuery
from .errors import GraphQLError, ResponseError, ResponseTypeError, error_class_map, error_code_map

try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    ProxyConnector = None


T = TypeVar("T")


@asynccontextmanager
async def sandboxed_get(url: URL) -> _RequestContextManager:
    async with ClientSession() as sess, sess.get(url) as resp:
        yield resp


zstd_dict = zstd.ZstdCompressionDict(data=pkgutil.get_data("maufbapi.http", "zstd-dict.dat"))
zstd_decomp = zstd.ZstdDecompressor(zstd_dict)


class BaseAndroidAPI:
    a_url = URL("https://api.facebook.com")
    b_url = URL("https://b-api.facebook.com")
    graph_url = URL("https://graph.facebook.com")
    b_graph_url = URL("https://b-graph.facebook.com")
    rupload_url = URL("https://rupload.facebook.com")
    http: ClientSession
    log: TraceLogger

    # Seems to be a per-minute request identifier
    _cid: str
    _cid_ts: int
    freeze_cid: bool
    # Seems to be a per-session request identifier
    nid: str
    # Seems to be a per-request incrementing integer
    _tid: int

    def __init__(self, state: AndroidState, log: TraceLogger | None = None) -> None:
        self.log = log or logging.getLogger("mauigpapi.http")

        connector = None
        try:
            http_proxy = urllib.request.getproxies()["http"]
        except KeyError:
            pass
        else:
            if ProxyConnector:
                connector = ProxyConnector.from_url(http_proxy)
            else:
                self.log.warning("http_proxy is set, but aiohttp-socks is not installed")

        self.http = ClientSession(connector=connector)
        self.state = state
        self._cid = ""
        self._cid_ts = 0
        self.freeze_cid = False
        self.nid = base64.b64encode(
            bytes([random.getrandbits(8) for _ in range(9)]),
        ).decode("utf-8")
        self._tid = 0
        self._file_url_cache = {}

    @property
    def tid(self) -> int:
        self._tid += 1
        return self._tid

    @property
    def cid(self) -> str:
        new_ts = int(time.time() / 60)
        if not self._cid or (self._cid_ts != new_ts and not self.freeze_cid):
            self._cid_ts = new_ts
            rand = random.Random(f"{self.state.device.uuid}{new_ts}")
            self._cid = bytes([rand.getrandbits(8) for _ in range(16)]).hex()
        return self._cid

    @property
    def session_id(self) -> str:
        return f"nid={self.nid};pid=Main;tid={self.tid};nc=0;fc=0;bc=0,cid={self.cid}"

    def format(self, req: dict[str, str], sign: bool = True, **extra: str) -> str:
        req = dict(sorted(req.items()))
        if sign:
            sig_data = "".join(f"{key}={value}" for key, value in req.items())
            sig_data_bytes = (sig_data + self.state.application.client_secret).encode("utf-8")
            req["sig"] = hashlib.md5(sig_data_bytes).hexdigest()
        if extra:
            req.update(extra)
        return "&".join(f"{quote(key)}={quote(value)}" for key, value in sorted(req.items()))

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "x-fb-connection-quality": self.state.device.connection_quality,
            "x-fb-connection-type": self.state.device.connection_type,
            "user-agent": self.state.user_agent,
            "x-tigon-is-retry": "False",
            "x-fb-http-engine": "Liger",
            "x-fb-client-ip": "True",
            "x-fb-server-cluster": "True",
            # "x-fb-connection-token": self.cid,
            # "x-fb-session-id": self.session_id,
            "x-fb-device-group": self.state.device.device_group,
            "x-fb-sim-hni": str(self.state.carrier.hni),
            "x-fb-net-hni": str(self.state.carrier.hni),
            "x-fb-rmd": "cached=0;state=NO_MATCH",
            "x-fb-request-analytics-tags": "unknown",
            # "x-fb-background-state": "1",
            "authorization": f"OAuth {self.state.session.access_token or 'null'}",
        }
        return {k: v for k, v in headers.items() if v is not None}

    @property
    def _params(self) -> dict[str, str]:
        return {
            "locale": self.state.device.language,
            "client_country_code": self.state.device.country_code,
        }

    def get(
        self,
        url: str | URL,
        headers: dict[str, str] | None = None,
        include_auth: bool = True,
        sandbox: bool = False,
        **kwargs,
    ) -> _RequestContextManager:
        headers = {
            **self._headers,
            **(headers or {}),
        }
        url = URL(url)
        if not url.host.endswith(".facebook.com") or not include_auth:
            headers.pop("authorization")
            if sandbox:
                return sandboxed_get(url)
        return self.http.get(url, headers=headers, **kwargs)

    async def graphql(
        self,
        req: GraphQLQuery,
        headers: dict[str, str] | None = None,
        response_type: Type[T] | None = JSON,
        path: list[str] | None = None,
        b: bool = True,
    ) -> T:
        headers = {
            **self._headers,
            **(headers or {}),
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": req.__class__.__name__,
            "x-fb-request-analytics-tags": "graphservice",
            "accept-encoding": "x-fb-dz;d=1, gzip, deflate",
        }
        variables = req.serialize()
        if isinstance(req, GraphQLMutation):
            variables = {"input": variables}
        params = {
            **self._params,
            "variables": json.dumps(variables),
            "method": "post",
            "doc_id": req.doc_id,
            "format": "json",
            "pretty": "false",
            # "query_name": req.__class__.__name__,
            "strip_defaults": "false",
            "strip_nulls": "false",
            "fb_api_req_friendly_name": req.__class__.__name__,
            "fb_api_caller_class": req.caller_class,
            "fb_api_analytics_tags": json.dumps(req.analytics_tags),
            "server_timestamps": "true",
        }
        if not req.include_client_country_code:
            params.pop("client_country_code")
        resp = await self.http.post(
            url=(self.b_graph_url if b else self.graph_url) / "graphql",
            data=params,
            headers=headers,
        )
        await self._decompress_zstd(resp)
        self.log.trace(f"GraphQL {req} response: {await resp.text()}")
        if response_type is None:
            self._handle_response_headers(resp)
            return None
        json_data = await self._handle_response(resp)
        if path:
            for item in path:
                json_data = json_data[item]
        if response_type is not JSON:
            return response_type.deserialize(json_data)
        return json_data

    async def _decompress_zstd(self, resp: ClientResponse) -> None:
        if (
            resp.headers.get("content-encoding") == "x-fb-dz"
            and resp.headers.get("x-fb-dz-dict") == "1"
            and not getattr(resp, "_zstd_decompressed", None)
        ):
            compressed = await resp.read()
            resp._body = zstd_decomp.decompress(compressed)
            self.log.trace(
                f"Decompressed {len(compressed)} bytes of zstd "
                f"into {len(resp._body)} bytes of (hopefully) JSON"
            )
            setattr(resp, "_zstd_decompressed", True)

    async def _handle_response(self, resp: ClientResponse, batch_index: int | None = None) -> JSON:
        await self._decompress_zstd(resp)
        self._handle_response_headers(resp)
        try:
            body = await resp.json()
        except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
            raise ResponseTypeError(resp.status, await resp.text()) from e
        if isinstance(body, list) and batch_index is not None:
            body = body[batch_index][1].get("body", {})
        error = body.get("error", None)
        errors = body.get("errors", [])
        if error:
            self.log.trace("Got error object in response data: %s", error)
            error_class = (
                error_code_map.get(error["code"])
                or error_class_map.get(error["type"])
                or ResponseError
            )
            raise error_class(error)
        elif errors:
            self.log.warning("Got list of errors in response data: %s", errors)
            if resp.status >= 400 or not body.get("data"):
                try:
                    raise GraphQLError(errors[0], errors[1:])
                except KeyError as e:
                    raise Exception("Unknown response error") from e
        return body

    def _handle_response_headers(self, resp: ClientResponse) -> None:
        # TODO if needed
        pass
