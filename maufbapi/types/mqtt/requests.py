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
from typing import Dict, List

from attr import dataclass

from maufbapi.thrift import TType, ThriftObject, field, autospec


@autospec
@dataclass(kw_only=True)
class SendMessageRequest(ThriftObject):
    # tfbid_<groupid> for groups, plain user id for users
    chat_id: str
    message: str
    offline_threading_id: int = field(TType.I64)
    # index 4: ???
    # Example values:
    #   'is_in_chatheads': 'false'
    #   'ld': '{"u":1674434.........}'
    #   'entrypoint': 'messenger_inbox:in_thread'
    #   'trigger': '2:thread_list:thread' or 'thread_view_messages_fragment_unknown'
    #   'active_now': '{"is_online":"false","last_active_seconds":"1431"}'
    #
    #   'media_camera_mode': 'VIDEO'
    #   'trigger': 'thread_view_messages_fragment_unknown'
    #   'entry_point': 'THREAD_CAMERA_COMPOSER_BUTTON'
    flags: Dict[str, str] = field(index=5, factory=lambda: {})
    # 369239263222822 = "like"
    sticker: str = field(default=None)
    # indices 7 and 8: ???
    media_ids: List[str] = field(default=None)
    # indices 10 and 11: ???
    sender_id: int = field(TType.I64, index=12)
    # indices 13-17: ???
    unknown_int32: int = field(TType.I32, index=18, default=0)
    # index 19: ???
    extra_metadata: Dict[str, str] = field(index=20, default=None)
    unknown_int64: int = field(TType.I64, index=21, default=0)
    # index 22: ???
    unknown_bool: bool = field(TType.BOOL, index=23, default=False)
    # this is weird int64 that looks like offline_threading_id, but isn't quite the same
    tid2: int = field(TType.I64, index=24)
    # indices 25-27: ???
    reply_to: str = field(index=28)


@autospec
@dataclass(kw_only=True)
class MarkReadRequest(ThriftObject):
    receipt_type: str = "read"
    unknown_boolean: bool = True
    # indices 3-5: ???
    group_id: int = field(TType.I64, index=6, default=None)
    user_id: int = field(TType.I64, default=None)
    # index 8: ???
    read_to: int = field(TType.I64, index=9)
    offline_threading_id: int = field(TType.I64, index=13)


@autospec
@dataclass
class ChatIDWrapper(ThriftObject):
    chat_id: str


@autospec
@dataclass
class OpenedThreadRequest(ThriftObject):
    unknown_i64: int = field(TType.I64, default=0)
    _chat_id: bytes = field(default=None)

    @property
    def chat_id(self) -> int:
        return int(ChatIDWrapper.from_thrift(self._chat_id).chat_id)

    @chat_id.setter
    def chat_id(self, value: int) -> None:
        self._chat_id = ChatIDWrapper(str(value)).to_thrift()
