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
from typing import Dict, Any, Optional


class ResponseError(Exception):
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        user_message = data.get("error_user_msg")
        if user_message:
            super().__init__(user_message)
        else:
            message = data["message"]
            code = data["code"]
            subcode = data.get("subcode")
            code_str = f"{code}.{subcode}" if subcode else str(code)
            super().__init__(f"{code_str}: {message}")


class OAuthException(ResponseError):
    pass


class InvalidAccessToken(OAuthException):
    pass


class TwoFactorRequired(OAuthException):
    user_message: str
    login_first_factor: str
    auth_token: str
    machine_id: Optional[str]
    uid: int

    def __init__(self, data: Dict[str, Any]) -> None:
        super().__init__(data)
        tfa_data = data["error_data"]
        self.login_first_factor = tfa_data["login_first_factor"]
        self.machine_id = tfa_data.get("machine_id")
        self.auth_token = tfa_data["auth_token"]
        self.uid = tfa_data["uid"]


class InvalidEmail(OAuthException):
    pass


class IncorrectPassword(OAuthException):
    pass


class GraphMethodException(ResponseError):
    pass


error_code_map = {
    190: InvalidAccessToken,
    400: InvalidEmail,
    401: IncorrectPassword,
    406: TwoFactorRequired,
}
_error_classes = (OAuthException, GraphMethodException)
error_class_map = {clazz.__name__: clazz for clazz in _error_classes}
