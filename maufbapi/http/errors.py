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
from typing import Optional


class ResponseError(Exception):
    def __init__(self, message: str, code: int, subcode: Optional[int]) -> None:
        self.message = message
        self.code = code
        self.subcode = subcode
        code_str = f"{code}.{subcode}" if subcode else str(code)
        super().__init__(f"{code_str}: {message}")


class OAuthException(ResponseError):
    pass


class GraphMethodException(ResponseError):
    pass


error_classes = [OAuthException, GraphMethodException]
error_class_map = {clazz.__name__: clazz for clazz in error_classes}
