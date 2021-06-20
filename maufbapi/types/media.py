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

from attr import dataclass
import attr

from mautrix.types import SerializableAttrs


@dataclass
class UploadErrorData(SerializableAttrs):
    retriable: bool
    type: str
    message: str


@dataclass
class UploadResponse(SerializableAttrs):
    media_id: Optional[int] = None
    err_code: Optional[str] = None
    err_str: Optional[str] = None
    is_retryable: Optional[str] = attr.ib(metadata={"json": "isRetryable"}, default=None)
    message_id: Optional[str] = None
    sent_by_server: Optional[str] = None
    success: Optional[str] = None
    debug_info: Optional[UploadErrorData] = None
