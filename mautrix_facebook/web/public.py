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
from typing import Optional, Dict
import logging
import random
import string
import time
import json

from aiohttp import web

from mautrix.types import UserID
from mautrix.util.signed_token import verify_token
from maufbapi import AndroidState, AndroidAPI
from maufbapi.http import TwoFactorRequired, OAuthException, IncorrectPassword

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
        for path in ("whoami", "login", "login/2fa", "login/check_approved", "login/approved",
                     "logout", "disconnect", "reconnect", "refresh"):
            self.app.router.add_options(f"/api/{path}", self.login_options)
        self.app.router.add_get("/api/whoami", self.status)
        self.app.router.add_post("/api/login", self.login)
        self.app.router.add_post("/api/login/2fa", self.login_2fa)
        self.app.router.add_get("/api/login/check_approved", self.login_check_approved)
        self.app.router.add_post("/api/login/approved", self.login_approved)
        self.app.router.add_post("/api/logout", self.logout)
        self.app.router.add_post("/api/disconnect", self.disconnect)
        self.app.router.add_post("/api/reconnect", self.reconnect)
        self.app.router.add_post("/api/refresh", self.refresh)

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
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            **self._acao_headers,
            "Content-Type": "application/json",
        }

    async def login_options(self, _: web.Request) -> web.Response:
        return web.Response(status=200, headers=self._headers)

    async def check_token(self, request: web.Request) -> Optional['u.User']:
        try:
            token = request.headers["Authorization"]
            token = token[len("Bearer "):]
        except KeyError:
            raise web.HTTPBadRequest(text='{"error": "Missing Authorization header"}',
                                     headers=self._headers)
        except IndexError:
            raise web.HTTPBadRequest(text='{"error": "Malformed Authorization header"}',
                                     headers=self._headers)
        if self.shared_secret and token == self.shared_secret:
            try:
                user_id = request.query["user_id"]
            except KeyError:
                raise web.HTTPBadRequest(text='{"error": "Missing user_id query param"}',
                                         headers=self._headers)
        else:
            user_id = self.verify_token(token)
            if not user_id:
                raise web.HTTPForbidden(text='{"error": "Invalid token"}', headers=self._headers)

        user = await u.User.get_by_mxid(user_id)
        return user

    async def status(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        data = {
            "permissions": user.permission_level,
            "mxid": user.mxid,
            "facebook": None,
        }
        if user.client:
            try:
                info = await user.client.get_self()
            except Exception:
                # TODO do something?
                self.log.warning("Exception while getting self from status endpoint",
                                 exc_info=True)
            else:
                data["facebook"] = info.serialize()
                data["facebook"]["connected"] = user.is_connected
                data["facebook"]["device_displayname"] = (f"{user.state.device.manufacturer} "
                                                          f"{user.state.device.name}")
        return web.json_response(data, headers=self._acao_headers)

    async def login(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text='{"error": "Malformed JSON"}', headers=self._headers)

        try:
            email = data["email"]
            password = data["password"]
        except KeyError:
            raise web.HTTPBadRequest(text='{"error": "Missing keys"}', headers=self._headers)

        state = AndroidState()
        state.generate(user.mxid)
        api = AndroidAPI(state, log=user.log.getChild("login-api"))
        try:
            await api.mobile_config_sessionless()
            await api.login(email, password)
            await user.on_logged_in(state)
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except TwoFactorRequired as e:
            user.command_status = {
                "action": "Login",
                "state": state,
                "api": api,
            }
            return web.json_response({
                "status": "two-factor",
                "error": e.data,
            }, headers=self._acao_headers)
        except OAuthException as e:
            return web.json_response({"error": str(e)}, headers=self._acao_headers)

    async def login_2fa(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(text='{"error": "No login in progress"}',
                                     headers=self._headers)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text='{"error": "Malformed JSON"}', headers=self._headers)

        try:
            email = data["email"]
            code = data["code"]
        except KeyError:
            raise web.HTTPBadRequest(text='{"error": "Missing keys"}', headers=self._headers)

        state: AndroidState = user.command_status["state"]
        api: AndroidAPI = user.command_status["api"]
        try:
            await api.login_2fa(email, code)
            await user.on_logged_in(state)
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except IncorrectPassword:
            return web.json_response({"error": "Incorrect two-factor authentication code",
                                      "status": "incorrect-code"}, headers=self._acao_headers)
        except OAuthException as e:
            return web.json_response({"error": str(e)}, headers=self._acao_headers)

    async def login_approved(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(text='{"error": "No login in progress"}',
                                     headers=self._headers)

        state: AndroidState = user.command_status["state"]
        api: AndroidAPI = user.command_status["api"]
        try:
            await api.login_approved()
            await user.on_logged_in(state)
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except OAuthException as e:
            return web.json_response({"error": str(e)}, headers=self._acao_headers)

    async def login_check_approved(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(text='{"error": "No login in progress"}',
                                     headers=self._headers)

        api: AndroidAPI = user.command_status["api"]
        approved = await api.check_approved_machine()
        return web.json_response({"approved": approved}, headers=self._acao_headers)

    async def logout(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        puppet = await pu.Puppet.get_by_fbid(user.fbid)
        await user.logout()
        if puppet.is_real_user:
            await puppet.switch_mxid(None, None)
        return web.json_response({}, headers=self._acao_headers)

    async def disconnect(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        if not user.is_connected:
            raise web.HTTPBadRequest(text='{"error": "User is not connected"}',
                                     headers=self._headers)
        user.mqtt.disconnect()
        await user.listen_task
        return web.json_response({}, headers=self._acao_headers)

    async def reconnect(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        if user.is_connected:
            raise web.HTTPConflict(text='{"error": "User is already connected"}',
                                   headers=self._headers)
        user.start_listen()
        return web.json_response({}, headers=self._acao_headers)

    async def refresh(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        await user.try_refresh()
        return web.json_response({}, headers=self._acao_headers)
