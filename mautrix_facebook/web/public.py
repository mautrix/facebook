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

import asyncio
import json
import logging
import random
import string
import time

from aiohttp import web
import pkg_resources

from maufbapi import AndroidAPI, AndroidState
from maufbapi.http import IncorrectPassword, OAuthException, TwoFactorRequired
from mautrix.types import UserID
from mautrix.util.signed_token import verify_token

from .. import puppet as pu, user as u
from .segment_analytics import init as init_segment, track


class InvalidTokenError(Exception):
    pass


class PublicBridgeWebsite:
    log: logging.Logger = logging.getLogger("mau.web.public")
    app: web.Application
    secret_key: str
    shared_secret: str
    ready_wait: asyncio.Future | None

    def __init__(
        self, shared_secret: str, segment_key: str | None, loop: asyncio.AbstractEventLoop
    ) -> None:
        self.app = web.Application()
        self.ready_wait = loop.create_future()
        self.secret_key = "".join(random.choices(string.ascii_lowercase + string.digits, k=64))
        self.shared_secret = shared_secret
        if segment_key:
            init_segment(segment_key)
        for path in (
            "whoami",
            "login",
            "login/prepare",
            "login/2fa",
            "login/check_approved",
            "login/approved",
            "logout",
            "disconnect",
            "reconnect",
            "refresh",
        ):
            self.app.router.add_options(f"/api/{path}", self.login_options)
        self.app.router.add_get("/api/whoami", self.status)
        self.app.router.add_post("/api/login/prepare", self.login_prepare)
        self.app.router.add_post("/api/login", self.login)
        self.app.router.add_post("/api/login/2fa", self.login_2fa)
        self.app.router.add_get("/api/login/check_approved", self.login_check_approved)
        self.app.router.add_post("/api/login/approved", self.login_approved)
        self.app.router.add_post("/api/logout", self.logout)
        self.app.router.add_post("/api/disconnect", self.disconnect)
        self.app.router.add_post("/api/reconnect", self.reconnect)
        self.app.router.add_post("/api/refresh", self.refresh)
        self.app.router.add_static(
            "/", pkg_resources.resource_filename("mautrix_facebook.web", "static/")
        )

    def verify_token(self, token: str) -> UserID:
        token = verify_token(self.secret_key, token)
        if token:
            if token.get("expiry", 0) < int(time.time()):
                raise InvalidTokenError("Access token has expired")
            return UserID(token.get("mxid"))
        raise InvalidTokenError("Access token is invalid")

    @property
    def _acao_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }

    @property
    def _headers(self) -> dict[str, str]:
        return {
            **self._acao_headers,
            "Content-Type": "application/json",
        }

    async def login_options(self, _: web.Request) -> web.Response:
        return web.Response(status=200, headers=self._headers)

    async def check_token(self, request: web.Request) -> u.User | None:
        if self.ready_wait:
            await self.ready_wait
            self.ready_wait = None
        try:
            token = request.headers["Authorization"]
            token = token[len("Bearer ") :]
        except KeyError:
            raise web.HTTPBadRequest(
                text='{"error": "Missing Authorization header"}', headers=self._headers
            )
        except IndexError:
            raise web.HTTPBadRequest(
                text='{"error": "Malformed Authorization header"}',
                headers=self._headers,
            )
        if self.shared_secret and token == self.shared_secret:
            try:
                user_id = request.query["user_id"]
            except KeyError:
                raise web.HTTPBadRequest(
                    text='{"error": "Missing user_id query param"}',
                    headers=self._headers,
                )
        else:
            try:
                user_id = self.verify_token(token)
            except InvalidTokenError as e:
                raise web.HTTPForbidden(
                    text=json.dumps(
                        {"error": f"{e}, please request a new one from the bridge bot"}
                    ),
                    headers=self._headers,
                )

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
                info = await user.get_own_info()
            except Exception:
                # TODO do something?
                self.log.warning(
                    "Exception while getting self from status endpoint", exc_info=True
                )
            else:
                data["facebook"] = info.serialize()
                data["facebook"]["connected"] = user.is_connected
                data["facebook"][
                    "device_displayname"
                ] = f"{user.state.device.manufacturer} {user.state.device.name}"
        return web.json_response(data, headers=self._acao_headers)

    async def login_prepare(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        state = user.generate_state()
        api = AndroidAPI(state, log=user.log.getChild("login-api"))
        user.command_status = {
            "action": "Login",
            "state": state,
            "api": api,
        }
        try:
            await api.mobile_config_sessionless()
        except Exception as e:
            self.log.exception(
                f"Failed to get mobile_config_sessionless to prepare login for {user.mxid}"
            )
            return web.json_response({"error": str(e)}, headers=self._acao_headers, status=500)
        return web.json_response(
            {
                "status": "login",
                "password_encryption_key_id": state.session.password_encryption_key_id,
                "password_encryption_pubkey": state.session.password_encryption_pubkey,
            },
            headers=self._acao_headers,
        )

    async def login(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text='{"error": "Malformed JSON"}', headers=self._headers)

        try:
            email = data["email"]
        except KeyError:
            raise web.HTTPBadRequest(text='{"error": "Missing email"}', headers=self._headers)
        try:
            password = data["password"]
            encrypted_password = None
        except KeyError:
            try:
                encrypted_password = data["encrypted_password"]
                password = None
            except KeyError:
                raise web.HTTPBadRequest(
                    text='{"error": "Missing password"}', headers=self._headers
                )

        if encrypted_password:
            if not user.command_status or user.command_status["action"] != "Login":
                raise web.HTTPBadRequest(
                    text='{"error": "No login in progress"}', headers=self._headers
                )
            state: AndroidState = user.command_status["state"]
            api: AndroidAPI = user.command_status["api"]
        else:
            state = user.generate_state()
            api = AndroidAPI(state, log=user.log.getChild("login-api"))
            await api.mobile_config_sessionless()

        try:
            track(user, "$login_start")
            self.log.debug(f"Logging in as {email} for {user.mxid}")
            resp = await api.login(email, password=password, encrypted_password=encrypted_password)
            self.log.debug(f"Got successful login response with UID {resp.uid} for {user.mxid}")
            await user.on_logged_in(state)
            track(user, "$login_success")
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except TwoFactorRequired as e:
            self.log.debug(
                f"Got 2-factor auth required login error with UID {e.uid} for {user.mxid}"
            )
            user.command_status = {
                "action": "Login",
                "state": state,
                "api": api,
            }
            return web.json_response(
                {
                    "status": "two-factor",
                    "error": e.data,
                },
                headers=self._acao_headers,
            )
        except OAuthException as e:
            track(user, "$login_failed", {"error": str(e)})
            self.log.debug(f"Got OAuthException {e} for {user.mxid}")
            return web.json_response({"error": str(e)}, headers=self._acao_headers, status=401)

    async def login_2fa(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(
                text='{"error": "No login in progress"}', headers=self._headers
            )

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text='{"error": "Malformed JSON"}', headers=self._headers)

        try:
            email = data["email"]
            code = data["code"]
        except KeyError as e:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": f"Missing key {e}"}), headers=self._headers
            )

        state: AndroidState = user.command_status["state"]
        api: AndroidAPI = user.command_status["api"]
        try:
            self.log.debug(f"Sending 2-factor auth code for {user.mxid}")
            resp = await api.login_2fa(email, code)
            self.log.debug(
                f"Got successful login response with UID {resp.uid} for {user.mxid}"
                " after 2fa login"
            )
            await user.on_logged_in(state)
            track(user, "$login_success")
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except IncorrectPassword:
            self.log.debug(f"Got incorrect 2fa code error for {user.mxid}")
            return web.json_response(
                {
                    "error": "Incorrect two-factor authentication code",
                    "status": "incorrect-code",
                },
                headers=self._acao_headers,
                status=401,
            )
        except OAuthException as e:
            track(user, "$login_failed", {"error": str(e)})
            self.log.debug(f"Got OAuthException {e} for {user.mxid} in 2fa stage")
            return web.json_response({"error": str(e)}, headers=self._acao_headers, status=401)

    async def login_approved(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(
                text='{"error": "No login in progress"}', headers=self._headers
            )

        state: AndroidState = user.command_status["state"]
        api: AndroidAPI = user.command_status["api"]
        try:
            self.log.debug(f"Trying to log in after approval for {user.mxid}")
            resp = await api.login_approved()
            self.log.debug(
                f"Got successful login response with UID {resp.uid} for {user.mxid}"
                " after approval login"
            )
            await user.on_logged_in(state)
            track(user, "$login_success")
            return web.json_response({"status": "logged-in"}, headers=self._acao_headers)
        except OAuthException as e:
            track(user, "$login_failed", {"error": str(e)})
            self.log.debug(f"Got OAuthException {e} for {user.mxid} in checkpoint login stage")
            return web.json_response({"error": str(e)}, headers=self._acao_headers, status=401)

    async def login_check_approved(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)

        if not user.command_status or user.command_status["action"] != "Login":
            raise web.HTTPBadRequest(
                text='{"error": "No login in progress"}', headers=self._headers
            )

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
            raise web.HTTPBadRequest(
                text='{"error": "User is not connected"}', headers=self._headers
            )
        user.mqtt.disconnect()
        await user.listen_task
        return web.json_response({}, headers=self._acao_headers)

    async def reconnect(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        if user.is_connected:
            raise web.HTTPConflict(
                text='{"error": "User is already connected"}', headers=self._headers
            )
        user.start_listen()
        return web.json_response({}, headers=self._acao_headers)

    async def refresh(self, request: web.Request) -> web.Response:
        user = await self.check_token(request)
        await user.refresh()
        return web.json_response({}, headers=self._acao_headers)
