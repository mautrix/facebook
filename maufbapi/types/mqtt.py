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
from typing import List, Dict

from attr import dataclass

from ..thrift import TType, RecursiveType, ThriftReader, field, autospec
from .responses import MessageUnsendability as Unsendability


@autospec
@dataclass
class BinaryThreadKey:
    other_user_id: int = field(TType.I64, default=None)
    thread_fbid: int = field(TType.I64, default=None)


@autospec
@dataclass(kw_only=True)
class MessageMetadata:
    thread: BinaryThreadKey
    id: str
    offline_threading_id: int = field(default=None)
    sender: int = field(TType.I64)
    timestamp: int = field(TType.I64)
    # index 6: unknown bool (ex: true)
    # index 7: ???
    tags: List[str] = field(index=8, factory=lambda: [])
    # index 9: unknown int32 (ex: 3)
    # index 10: unknown bool (ex: false)
    message_unsendability: Unsendability = field(TType.BINARY, index=12,
                                                 default=Unsendability.DENY_FOR_NON_SENDER)
    # indices 13-16: ???
    # index 17: struct
    #   index 1: int64 group id
    #   index 2: set of int64 recipient user ids in private chats?


@autospec
@dataclass(kw_only=True)
class ImageInfo:
    original_width: int = field(TType.I32)
    original_height: int = field(TType.I64)
    previews: Dict[int, str] = field(RecursiveType(TType.MAP, key_type=TType.I32,
                                                   value_type=RecursiveType(TType.BINARY)))
    # index 4: unknown int32
    # indices 5-7: ???
    image_type: str = field(index=8)
    # index 9: ???
    # index 10: unknown bool


@autospec
@dataclass(kw_only=True)
class Attachment:
    media_id_str: str
    mime_type: str
    file_name: str
    media_id: int = field(TType.I64)
    file_size: int = field(TType.I64)
    # indices 6-9: ???
    image_info: ImageInfo = field(default=None, index=10)
    # index 1007?!: unknown bool


@autospec
@dataclass(kw_only=True)
class Reaction:
    thread: BinaryThreadKey
    message_id: str
    # index 3: unknown int32 (zero)
    reaction_sender_id: int = field(TType.I64, index=4)
    reaction: str
    message_sender_id: int = field(TType.I64)
    # index 7: unknown number as string, similar to MessageMetadata's index 3


@autospec
@dataclass(kw_only=True)
class Message:
    metadata: MessageMetadata
    text: str
    # index 3: ???
    # index 4: possibly int32 (ex: zero)
    attachments: List[Attachment] = field(index=5, factory=lambda: [])
    # index 6: some sort of struct:
    #    1: List[BinaryThreadKey]?
    #    2: ???
    #    3: timestamp?
    #    4: timestamp?
    # index 7: mysterious map
    # index 1000?!: int64 (ex: 81)
    # index 1017: int64 (ex: 924)
    # index 1003: struct
    #   index 1: struct
    #     index 1: binary, replying to message id
    # index 1012: map<binary, binary>
    #   key apiArgs: binary containing thrift
    #     index 2: binary url, https://www.facebook.com/intern/agent/realtime_delivery/
    #     index 4: int64 (ex: 0)
    #     index 7: binary, empty?
    #     index 5: binary, some sort of uuid
    #     index 8: list<map>
    #       item 1: map<binary, binary>
    #         {"layer": "www", "push_phase": "C3", "www_rev": "1003179603",
    #          "buenopath": "XRealtimeDeliveryThriftServerController:sendRealtimeDeliveryRequest:/ls_req:TASK_LABEL=SEND_MESSAGE_V{N}"}
    #     index 9: binary (ex: www)
    #     index 10: boolean (ex: false)
    # index 1015: list<binary>, some sort of tags


@autospec
@dataclass(kw_only=True)
class ExtendedMessage:
    reply_to_message: Message
    message: Message


@autospec
@dataclass
class MessageSyncInnerEvent:
    reaction: Reaction = field(index=10, default=None)
    extended_message: ExtendedMessage = field(index=55, default=None)


@autospec
@dataclass
class MessageSyncInnerPayload:
    items: List[MessageSyncInnerEvent]


@autospec
@dataclass(kw_only=True)
class BinaryData:
    data: bytes

    def parse(self) -> MessageSyncInnerPayload:
        return ThriftReader(self.data).read_struct(MessageSyncInnerPayload)


@autospec
@dataclass(kw_only=True)
class MessageSyncEvent:
    data: Message = field(index=2, default=None)
    binary: BinaryData = field(index=42, default=None)

    first_seq_id: int = field(TType.I64, index=1, default=None)
    last_seq_id: int = field(TType.I64, index=2, secondary=True, default=None)
    viewer: int = field(TType.I64, index=3, default=None)


@autospec
@dataclass(kw_only=True)
class MessageSyncPayload:
    items: List[MessageSyncEvent] = field(factory=lambda: [])
    first_seq_id: int = field(TType.I64, default=None)
    last_seq_id: int = field(TType.I64, default=None)
    viewer: int = field(TType.I64, default=None)
    # index 11: unknown string, contains "1"
    error: str = field(index=12, default=None)


@autospec
@dataclass
class SendMessageRequest:
    # tfbid_<groupid> for groups, plain user id for users
    chat_id: str
    message: str
    offline_threading_id: int = field(TType.I64)
    # index 4: ???
    # Example values:
    #   'is_in_chatheads': 'false'
    #   'ld': '{"u":1674434246165228}'
    #   'trigger': '2:thread_list:thread'
    #   'active_now': '{"is_online":"false","last_active_seconds":"1431"}'
    extra_meta: Dict[str, str] = field(index=5)
    # indices 6-11: ???
    sender_id: int = field(TType.I64, index=12)
    # indices 13-17: ???
    unknown_int32: int = field(TType.I32, index=18, default=0)
    # indices 19 and 20: ???
    unknown_int64: int = field(TType.I64, index=21, default=0)
    # index 22: ???
    unknown_bool: bool = field(TType.BOOL, index=23, default=False)
    # index 24: a weird int64 that looks like offline_threading_id, but isn't quite the same
