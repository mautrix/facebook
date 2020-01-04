# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Optional
from http.cookies import SimpleCookie
import logging
import random
import string
import time
import json

from aiohttp import web
import pkg_resources

from mautrix.types import UserID
from mautrix.util.signed_token import verify_token

from .. import user as u


class PublicBridgeWebsite:
    log: logging.Logger = logging.getLogger("ma.web.public")
    app: web.Application
    secret_key: str

    def __init__(self) -> None:
        self.app = web.Application()
        self.secret_key = "".join(random.choices(string.ascii_lowercase + string.digits, k=64))
        self.app.router.add_static("/", pkg_resources.resource_filename("mautrix_facebook",
                                                                        "web/static/"))
        self.app.router.add_options("/api/login", self.login_options)
        self.app.router.add_post("/api/login", self.login)

    def verify_token(self, token: str) -> Optional[UserID]:
        token = verify_token(self.secret_key, token)
        if token and token.get("expiry", 0) > int(time.time()):
            return UserID(token.get("mxid"))
        return None

    @staticmethod
    async def login_options(_: web.Request) -> web.Response:
        return web.Response(status=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        })

    async def login(self, request: web.Request) -> web.Response:
        headers = (await self.login_options(request)).headers
        try:
            token = request.headers["Authorization"]
            token = token[len("Bearer "):]
            user_id = self.verify_token(token)
        except KeyError:
            return web.json_response({"error": "Missing Authorization header"}, status=403,
                                     headers=headers)
        except IndexError:
            return web.json_response({"error": "Malformed Authorization header"}, status=401,
                                     headers=headers)
        if not user_id:
            return web.json_response({"error": "Invalid token"}, status=401, headers=headers)

        try:
            user_agent = request.headers["User-Agent"]
        except KeyError:
            return web.json_response({"error": "Missing User-Agent header"}, status=400,
                                     headers=headers)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Malformed JSON"}, status=400, headers=headers)

        user = u.User.get_by_mxid(user_id)
        cookie = SimpleCookie()
        cookie["c_user"] = data["c_user"]
        cookie["xs"] = data["xs"]
        user.user_agent = user_agent
        user.save()
        ok = await user.set_session(cookie, user_agent) and await user.is_logged_in(True)
        if not ok:
            return web.json_response({"error": "Facebook authorization failed"}, status=401,
                                     headers=headers)
        await user.on_logged_in(data["c_user"])
        if user.command_status and user.command_status.get("action") == "Login":
            user.command_status = None
        return web.json_response({}, status=200, headers=headers)
