# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2021 Tulir Asokan
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
from typing import Optional, Dict, TypeVar, Type
from urllib.parse import quote
import hashlib
import logging
import base64
import random
import json
import time

from mautrix.types import JSON
from aiohttp import ClientSession, ClientResponse
from mautrix.util.logging import TraceLogger
from yarl import URL

from ..state import AndroidState
from ..types import GraphQLQuery

T = TypeVar('T')


class BaseAndroidAPI:
    a_url = URL("https://api.facebook.com")
    b_url = URL("https://b-api.facebook.com")
    graph_url = URL("https://graph.facebook.com")
    b_graph_url = URL("https://b-graph.facebook.com")
    graphql_url = graph_url / "graphql"
    http: ClientSession
    log: TraceLogger

    # Seems to be a per-minute request identifier
    _cid: str
    _cid_ts: int
    # Seems to be a per-session request identifier
    nid: str
    # Seems to be a per-request incrementing integer
    _tid: int

    def __init__(self, state: AndroidState, log: Optional[TraceLogger] = None) -> None:
        self.http = ClientSession()
        self.state = state
        self.log = log or logging.getLogger("mauigpapi.http")
        self._cid_ts = 0
        self.nid = base64.b64encode(bytes([random.getrandbits(8) for _ in range(9)])
                                    ).decode("utf-8")
        self._tid = 0

    @property
    def tid(self) -> int:
        self._tid += 1
        return self._tid

    @property
    def cid(self) -> str:
        new_ts = int(time.time() / 60)
        if self._cid_ts != new_ts:
            self._cid_ts = new_ts
            rand = random.Random(f"{self.state.device.uuid}{new_ts}")
            self._cid = bytes([rand.getrandbits(8) for _ in range(16)]).hex()
        return self._cid

    @property
    def session_id(self) -> str:
        return f"nid={self.nid};pid=Main;tid={self.tid};nc=0;fc=0;bc=0,cid={self.cid}"

    def format(self, req: Dict[str, str], sign: bool = True, **extra: str) -> str:
        req = dict(sorted(req.items()))
        if sign:
            sig_data = "".join(f"{key}={value}" for key, value in req.items())
            sig_data_bytes = (sig_data + self.state.application.client_secret).encode("utf-8")
            req["sig"] = hashlib.md5(sig_data_bytes).hexdigest()
        if extra:
            req.update(extra)
        import pprint
        pprint.pprint(req)
        return "&".join(f"{quote(key)}={quote(value)}" for key, value in sorted(req.items()))

    @property
    def _headers(self) -> Dict[str, str]:
        headers = {
            "x-fb-connection-quality": self.state.device.connection_quality,
            "x-fb-connection-type": self.state.device.connection_type,
            "user-agent": self.state.user_agent,
            "x-tigon-is-retry": "False",
            "x-fb-http-engine": "Liger",
            "x-fb-client-ip": "True",
            "x-fb-connection-token": self.cid,
            "x-fb-session-id": self.session_id,
            "x-fb-device-group": self.state.device.device_group,
            "x-fb-sim-hni": str(self.state.carrier.hni),
            "x-fb-net-hni": str(self.state.carrier.hni),
            # "x-fb-background-state": "1",
            "authorization": f"OAuth {self.state.session.access_token or 'null'}",
        }
        return {k: v for k, v in headers.items() if v is not None}

    @property
    def _params(self) -> Dict[str, str]:
        return {
            "locale": self.state.device.language,
            "client_country_code": self.state.device.country_code,
        }

    async def graphql(self, req: GraphQLQuery, headers: Optional[Dict[str, str]] = None,
                      response_type: Optional[Type[T]] = JSON) -> T:
        headers = {
            **self._headers,
            **(headers or {}),
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": req.__class__.__name__,
        }
        params = {
            **self._params,
            "variables": json.dumps(req.serialize()),
            "method": "post",
            "doc_id": req.doc_id,
            "format": "json",
            "pretty": "false",
            "query_name": req.__class__.__name__,
            "strip_defaults": "false",
            "strip_nulls": "false",
            "fb_api_req_friendly_name": req.__class__.__name__,
            "fb_api_caller_class": "graphservice",
        }
        resp = await self.http.post(url=self.graphql_url, data=params, headers=headers)
        self.log.trace(f"GraphQL {req} response: {await resp.text()}")
        if response_type is None:
            self._handle_response_headers(resp)
            return None
        json_data = await self._handle_response(resp)
        if response_type is not JSON:
            return response_type.deserialize(json_data)
        return json_data

    async def _handle_response(self, resp: ClientResponse) -> JSON:
        self._handle_response_headers(resp)
        body = await resp.json()
        return body
        # if body.get("status", "fail") == "ok":
        #     return body
        # else:
        #     await self._raise_response_error(resp)

    async def _raise_response_error(self, resp: ClientResponse) -> None:
        try:
            data = await resp.json()
        except json.JSONDecodeError:
            data = {}
        # TODO if needed

    def _handle_response_headers(self, resp: ClientResponse) -> None:
        # TODO if needed
        pass
