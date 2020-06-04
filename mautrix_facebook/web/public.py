# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import Optional, Dict, cast
import logging
import random
import string
import time
import json

from aiohttp import web
import pkg_resources
import attr

import fbchat

from mautrix.types import UserID
from mautrix.util.signed_token import verify_token

from .. import user as u, puppet as pu


class PublicBridgeWebsite:
    log: logging.Logger = logging.getLogger("mau.web.public")
    app: web.Application
    secret_key: str
    shared_secret: str

    def __init__(self, shared_secret: str) -> None:
        self.app = web.Application()
        self.secret_key = "".join(random.choices(string.ascii_lowercase + string.digits, k=64))
        self.shared_secret = shared_secret
        self.app.router.add_get("/api/whoami", self.status)
        self.app.router.add_options("/api/login", self.login_options)
        self.app.router.add_post("/api/login", self.login)
        self.app.router.add_post("/api/logout", self.logout)
        self.app.router.add_static("/", pkg_resources.resource_filename("mautrix_facebook",
                                                                        "web/static/"))

    def verify_token(self, token: str) -> Optional[UserID]:
        token = verify_token(self.secret_key, token)
        if token and token.get("expiry", 0) > int(time.time()):
            return UserID(token.get("mxid"))
        return None

    @property
    def _acao_headers(self) -> Dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        }

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            **self._acao_headers,
            "Content-Type": "application/json",
        }

    async def login_options(self, _: web.Request) -> web.Response:
        return web.Response(status=200, headers=self._headers)

    def check_token(self, request: web.Request) -> Optional['u.User']:
        try:
            token = request.headers["Authorization"]
            token = token[len("Bearer "):]
        except KeyError:
            raise web.HTTPBadRequest(body='{"error": "Missing Authorization header"}',
                                     headers=self._headers)
        except IndexError:
            raise web.HTTPBadRequest(body='{"error": "Malformed Authorization header"}',
                                     headers=self._headers)
        if self.shared_secret and token == self.shared_secret:
            try:
                user_id = request.query["user_id"]
            except KeyError:
                raise web.HTTPBadRequest(body='{"error": "Missing user_id query param"}',
                                         headers=self._headers)
        else:
            user_id = self.verify_token(token)
            if not user_id:
                raise web.HTTPForbidden(body='{"error": "Invalid token"}', headers=self._headers)

        user = u.User.get_by_mxid(user_id)
        return user

    async def status(self, request: web.Request) -> web.Response:
        user = self.check_token(request)
        data = {
            "permissions": user.permission_level,
            "mxid": user.mxid,
            "facebook": None,
        }
        if await user.is_logged_in():
            info = cast(fbchat.UserData, await user.client.fetch_thread_info([user.fbid]).__anext__())
            data["facebook"] = attr.asdict(info)
            del data["facebook"]["session"]
        return web.json_response(data, headers=self._acao_headers)

    async def login(self, request: web.Request) -> web.Response:
        user = self.check_token(request)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(body='{"error": "Malformed JSON"}', headers=self._headers)

        try:
            session = await fbchat.Session.from_cookies(data)
        except fbchat.FacebookError:
            self.log.debug("Failed to log in", exc_info=True)
            raise web.HTTPUnauthorized(body='{"error": "Facebook authorization failed"}',
                                       headers=self._headers)
        if not await session.is_logged_in():
            raise web.HTTPUnauthorized(body='{"error": "Facebook authorization failed"}',
                                       headers=self._headers)
        await user.on_logged_in(session)
        if user.command_status and user.command_status.get("action") == "Login":
            user.command_status = None
        return web.Response(body='{}', status=200, headers=self._headers)

    async def logout(self, request: web.Request) -> web.Response:
        user = self.check_token(request)

        puppet = pu.Puppet.get_by_fbid(user.fbid)
        await user.logout()
        if puppet.is_real_user:
            await puppet.switch_mxid(None, None)
        return web.json_response({}, headers=self._acao_headers)
