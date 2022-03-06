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

from typing import Any


class ResponseError(Exception):
    def __init__(self, data: dict[str, Any], other_error_count: int = 0) -> None:
        self.data = data
        user_message = data.get("error_user_msg")
        if user_message:
            super().__init__(user_message)
        else:
            message = data["message"]
            code = data.get("code", "")
            subcode = data.get("subcode") or data.get("error_subcode")
            code_str = f"{code}.{subcode}" if subcode else str(code)
            if other_error_count > 0:
                message += f" (and {other_error_count} other errors)"
            super().__init__(f"{code_str}: {message}" if code_str else message)


class ResponseTypeError(ResponseError):
    def __init__(self, status: int, body: str) -> None:
        Exception.__init__(self, f"Got non-JSON response with status {status}: {body}")


class GraphQLError(ResponseError):
    def __init__(self, first: dict[str, Any], rest: list[dict[str, Any]]) -> None:
        super().__init__(first, other_error_count=len(rest))
        self.others = rest


class OAuthException(ResponseError):
    pass


class InvalidAccessToken(OAuthException):
    pass


class TwoFactorRequired(OAuthException):
    user_message: str
    login_first_factor: str
    auth_token: str
    machine_id: str | None
    uid: int

    def __init__(self, data: dict[str, Any]) -> None:
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
