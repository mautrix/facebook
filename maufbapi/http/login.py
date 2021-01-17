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
import base64
import struct
import time
import io

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, AES
from Crypto.Random import get_random_bytes

from .base import BaseAndroidAPI
from .errors import TwoFactorRequired
from ..types import LoginResponse, PasswordKeyResponse, MobileConfig


class LoginAPI(BaseAndroidAPI):
    async def pwd_key_fetch(self) -> PasswordKeyResponse:
        req_data = self.format({
            "version": "2",
            "flow": "CONTROLLER_INITIALIZATION",
            **self._params,
            "method": "GET",
            "fb_api_req_friendly_name": "pwdKeyFetch",
            "fb_api_caller_class": "com.facebook.auth.login.AuthOperations",
            "access_token": self.state.application.access_token,
        }, sign=False)
        resp = await self.http.post(self.graph_url.with_path("//pwd_key_fetch"),
                                    headers=self._headers, data=req_data)
        json_data = await self._handle_response(resp)
        parsed = PasswordKeyResponse.deserialize(json_data)
        self.state.session.password_encryption_pubkey = parsed.public_key
        self.state.session.password_encryption_key_id = parsed.key_id
        return parsed

    async def mobile_config_sessionless(self) -> MobileConfig:
        req_data = self.format({
            "query_hash": "4d8d81394cc798f2a72f5761248743fa7f4d3c6b0048f7e9696006230f118896",
            "one_query_hash": "b1f0cf90cfb2d8424ecb5f82c9d9365a03bf7f3af3315df1c8d9f0b5e4818bdd",
            "bool_opt_policy": "1",
            "device_id": self.state.device.uuid,
            "api_version": "7",
            "name_to_id": "true",
            "fetch_type": "SYNC_FULL",
            "access_token": self.state.application.access_token,
            **self._params,
        }, sign=False)
        resp = await self.http.post(self.b_graph_url / "mobileconfigsessionless",
                                    headers=self._headers, data=req_data)
        json_data = await self._handle_response(resp)
        parsed = MobileConfig.deserialize(json_data)
        self.state.session.password_encryption_key_id = parsed.find(15712, 1).i64
        self.state.session.password_encryption_pubkey = parsed.find(15712, 2).str
        return parsed

    async def login(self, email: str, password: Optional[str] = None,
                    encrypted_password: Optional[str] = None) -> LoginResponse:
        if password:
            if encrypted_password:
                raise ValueError("Only one of password or encrypted_password must be provided")
            encrypted_password = self._encrypt_password(password)
        elif not encrypted_password:
            raise ValueError("One of password or encrypted_password is required")
        return await self._login(email=email, password=encrypted_password,
                                 credentials_type="password")

    async def login_2fa(self, email: str, code: str) -> LoginResponse:
        if not self.state.session.login_first_factor:
            raise ValueError("No two-factor login in progress")
        return await self._login(email=email, password=code, twofactor_code=code,
                                 encrypted_msisdn="", currently_logged_in_userid="0",
                                 userid=str(self.state.session.uid),
                                 machine_id=self.state.session.machine_id,
                                 first_factor=self.state.session.login_first_factor,
                                 credentials_type="two_factor")

    async def login_approved(self) -> LoginResponse:
        if not self.state.session.transient_auth_token:
            raise ValueError("No two-factor login in progress")
        return await self._login(password=self.state.session.transient_auth_token,
                                 email=str(self.state.session.uid), encrypted_msisdn="",
                                 credentials_type="transient_token")

    async def check_approved_machine(self) -> bool:
        req: Dict[str, str] = {
            "u": str(self.state.session.uid),
            "m": self.state.session.machine_id,
            **self._params,
            "method": "GET",
            "fb_api_req_friendly_name": "checkApprovedMachine",
            "fb_api_caller_class": "com.facebook.account.twofac.protocol.TwoFacServiceHandler",
            "access_token": self.state.application.access_token,
        }
        headers = {
            **self._headers,
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": req["fb_api_req_friendly_name"],
        }
        resp = await self.http.post(url=self.graph_url / "check_approved_machine",
                                    headers=headers, data=req)
        json_data = await self._handle_response(resp)
        return json_data["data"][0]["approved"]

    async def _login(self, **kwargs) -> LoginResponse:
        req: Dict[str, str] = {
            **self._params,
            "adid": self.state.device.adid,
            "api_key": self.state.application.client_id,
            "community_id": "",
            "cpl": "true",
            "currently_logged_in_userid": "0",
            "device_id": self.state.device.uuid,
            "fb_api_caller_class": "com.facebook.auth.login.AuthOperations$PasswordAuthOperation",
            "fb_api_req_friendly_name": "authenticate",
            "format": "json",
            "generate_analytics_claim": "1",
            "generate_machine_id": "1",
            "generate_session_cookies": "1",
            "jazoest": self._jazoest,
            "meta_inf_fbmeta": "NO_FILE",
            "source": "login",
            "try_num": "1",  # TODO maybe cache this somewhere?
            **kwargs,
        }
        req_data = self.format(req, sign=True, access_token=self.state.application.access_token)
        headers = {
            **self._headers,
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": req["fb_api_req_friendly_name"],
        }
        resp = await self.http.post(url=self.b_graph_url / "auth" / "login",
                                    headers=headers, data=req_data)
        self.log.trace(f"Login response: {resp.status} {await resp.text()}")
        try:
            json_data = await self._handle_response(resp)
        except TwoFactorRequired as e:
            self.state.session.machine_id = e.machine_id
            self.state.session.uid = e.uid
            self.state.session.login_first_factor = e.login_first_factor
            self.state.session.transient_auth_token = e.auth_token
            raise
        parsed = LoginResponse.deserialize(json_data)
        self.state.session.access_token = parsed.access_token
        self.state.session.uid = parsed.uid
        self.state.session.machine_id = parsed.machine_id
        self.state.session.login_first_factor = None
        # TODO maybe store the cookies and other data too?
        return parsed

    def _encrypt_password(self, password: str) -> str:
        # Key and IV for AES encryption
        rand_key = get_random_bytes(32)
        iv = get_random_bytes(12)

        # Encrypt AES key with Facebook's RSA public key
        pubkey_bytes = self.state.session.password_encryption_pubkey
        pubkey = RSA.import_key(pubkey_bytes)
        cipher_rsa = PKCS1_v1_5.new(pubkey)
        encrypted_rand_key = cipher_rsa.encrypt(rand_key)

        cipher_aes = AES.new(rand_key, AES.MODE_GCM, nonce=iv)
        # Add the current time to the additional authenticated data (AAD) section
        current_time = int(time.time())
        cipher_aes.update(str(current_time).encode("utf-8"))
        # Encrypt the password and get the AES MAC auth tag
        encrypted_passwd, auth_tag = cipher_aes.encrypt_and_digest(password.encode("utf-8"))

        buf = io.BytesIO()
        # 1 is presumably the version
        buf.write(bytes([1, int(self.state.session.password_encryption_key_id)]))
        buf.write(iv)
        # Length of the encrypted AES key as a little-endian 16-bit int
        buf.write(struct.pack("<h", len(encrypted_rand_key)))
        buf.write(encrypted_rand_key)
        buf.write(auth_tag)
        buf.write(encrypted_passwd)
        encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"#PWD_MSGR:1:{current_time}:{encoded}"

    @property
    def _jazoest(self) -> str:
        return f"2{sum(ord(i) for i in self.state.device.uuid)}"
