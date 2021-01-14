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
from typing import List, Dict, Optional, Any
from enum import Enum
import json

from attr import dataclass
import attr

from mautrix.types import SerializableAttrs
from maufbapi.thrift import TType, RecursiveType, ThriftObject, field, autospec
from ..common import MessageUnsendability as Unsendability


@autospec
@dataclass
class ThreadKey(ThriftObject):
    other_user_id: int = field(TType.I64, default=None)
    thread_fbid: int = field(TType.I64, default=None)

    @property
    def id(self) -> Optional[int]:
        if self.other_user_id:
            return self.other_user_id
        elif self.thread_fbid:
            return self.thread_fbid
        else:
            return None


@autospec
@dataclass(kw_only=True)
class MessageMetadata(ThriftObject):
    thread: ThreadKey
    id: str
    offline_threading_id: int = field(TType.I64, default=None)
    sender: int = field(TType.I64)
    timestamp: int = field(TType.I64)
    # index 6: unknown bool (ex: true)
    action_summary: str = field(index=7, default=None)
    tags: List[str] = field(factory=lambda: [])
    # index 9: unknown int32 (ex: 3)
    # index 10: unknown bool (ex: false)
    message_unsendability: Unsendability = field(TType.BINARY, index=12,
                                                 default=Unsendability.DENY_FOR_NON_SENDER)
    # indices 13-16: ???
    # index 17: struct (or maybe union?)
    #   index 1: int64 group id
    #   index 2: set of int64 recipient user ids in private chats?


@autospec
@dataclass(kw_only=True)
class ImageInfo(ThriftObject):
    original_width: int = field(TType.I32)
    original_height: int = field(TType.I32)
    previews: Dict[int, str] = field(TType.MAP, key_type=TType.I32, default=None,
                                     value_type=RecursiveType(TType.BINARY, python_type=str))
    # index 4: unknown int32
    # indices 5 and 6: ???
    alt_previews: Dict[int, str] = field(TType.MAP, key_type=TType.I32, default=None, index=7,
                                         value_type=RecursiveType(TType.BINARY, python_type=str))
    image_type: str = field(default=None)
    alt_preview_type: str = field(default=None)
    # index 9: ???
    # index 10: unknown bool


@autospec
@dataclass(kw_only=True)
class VideoInfo(ThriftObject):
    original_width: int = field(TType.I32)
    original_height: int = field(TType.I32)
    duration_ms: int = field(TType.I32)
    thumbnail_url: str
    download_url: str
    # index 6: unknown int32 (ex: 1)
    # index 7: unknown int32 (ex: 0)
    # index 8: unknown int32 (ex: 0)


@autospec
@dataclass(kw_only=True)
class Attachment(ThriftObject):
    media_id_str: str
    mime_type: str
    file_name: str
    media_id: int = field(TType.I64)
    file_size: int = field(TType.I64, default=None)
    # indices 6-9: ???
    image_info: ImageInfo = field(default=None, index=10)
    video_info: VideoInfo = field(default=None, index=11)
    # index 12: ???
    # can contain a dash_manifest key with some XML as the value
    # or fbtype key with a number as value
    extra_metadata: Dict[str, str] = field(index=13)
    # index 1007?!: unknown bool


@autospec
@dataclass(kw_only=True)
class Reaction(ThriftObject):
    thread: ThreadKey
    message_id: str
    # index 3: unknown int32 (zero)
    reaction_sender_id: int = field(TType.I64, index=4)
    reaction: str
    message_sender_id: int = field(TType.I64)
    # index 7: unknown number as string, similar to MessageMetadata's index 3


class MentionType(Enum):
    PERSON = "p"


@dataclass
class Mention(SerializableAttrs['Mention']):
    offset: int = attr.ib(metadata={"json": "o"})
    length: int = attr.ib(metadata={"json": "l"})
    user_id: str = attr.ib(metadata={"json": "i"})
    type: MentionType = attr.ib(metadata={"json": "t"}, default=MentionType.PERSON)


@autospec
@dataclass(kw_only=True)
class Message(ThriftObject):
    metadata: MessageMetadata
    text: str = field(default=None)
    # index 3: ???
    sticker: int = field(TType.I64, index=4, default=None)
    attachments: List[Attachment] = field(factory=lambda: [])
    # index 6: some sort of struct:
    #    1: List[BinaryThreadKey]?
    #    2: ???
    #    3: timestamp?
    #    4: timestamp?
    extra_metadata: Dict[str, bytes] = field(index=7, factory=lambda: {})

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

    @property
    def mentions(self) -> List[Mention]:
        return [Mention.deserialize(item) for item
                in json.loads(self.extra_metadata.get("prng", "[]"))]


@autospec
@dataclass(kw_only=True)
class ExtendedMessage(ThriftObject):
    reply_to_message: Message
    message: Message


@autospec
@dataclass(kw_only=True)
class UnsendMessage(ThriftObject):
    thread: ThreadKey
    message_id: str
    timestamp: int = field(TType.I64)
    user_id: int = field(TType.I64)
    # index 5: unknown int64 (ex: 0)


@autospec
@dataclass
class MessageSyncInnerEvent(ThriftObject):
    reaction: Reaction = field(index=10, default=None)
    extended_message: ExtendedMessage = field(index=55, default=None)
    unsend_message: UnsendMessage = field(index=67, default=None)


@autospec
@dataclass
class MessageSyncInnerPayload(ThriftObject):
    items: List[MessageSyncInnerEvent]


@autospec
@dataclass(kw_only=True)
class BinaryData(ThriftObject):
    data: bytes

    def parse(self) -> MessageSyncInnerPayload:
        return MessageSyncInnerPayload.from_thrift(self.data)


@autospec
@dataclass
class ReadReceipt(ThriftObject):
    thread: ThreadKey
    user_id: int = field(TType.I64)
    read_at: int = field(TType.I64)
    read_to: int = field(TType.I64)


@autospec
@dataclass
class OwnReadReceipt(ThriftObject):
    threads: List[ThreadKey]
    # index 2: ???
    read_to: int = field(TType.I64, index=3)
    read_at: int = field(TType.I64)


@autospec
@dataclass
class NameChange(ThriftObject):
    metadata: MessageMetadata
    new_name: str


@autospec
@dataclass
class AvatarChange(ThriftObject):
    metadata: MessageMetadata
    new_avatar: Attachment


# TODO are there others?
class EmojiChangeAction(Enum):
    CHANGE_THREAD_ICON = "change_thread_icon"


@autospec
@dataclass(kw_only=True)
class EmojiChange(ThriftObject):
    metadata: MessageMetadata
    action: EmojiChangeAction = field(TType.BINARY)
    action_data: Dict[str, str]


@autospec
@dataclass(kw_only=True)
class MessageSyncEvent(ThriftObject):
    message: Message = field(index=2, default=None)
    own_read_receipt: OwnReadReceipt = field(index=4, default=None)
    name_change: NameChange = field(index=10, default=None)
    avatar_change: AvatarChange = field(index=11, default=None)
    emoji_change: EmojiChange = field(index=17, default=None)
    read_receipt: ReadReceipt = field(index=19, default=None)
    # index 25: unknown struct
    #   index 1: ThreadKey
    #   index 2: some user ID
    #   index 6: list of binary (message IDs)
    #   index 7: timestamp
    binary: BinaryData = field(index=42, default=None)

    def get_parts(self) -> List[Any]:
        parts = [self.message, self.own_read_receipt, self.name_change, self.avatar_change,
                 self.emoji_change, self.read_receipt]
        if self.binary:
            for inner_item in self.binary.parse().items:
                parts += [inner_item.reaction, inner_item.extended_message,
                          inner_item.unsend_message]
        return [part for part in parts if part is not None]


class MessageSyncError(Enum):
    QUEUE_OVERFLOW = "ERROR_QUEUE_OVERFLOW"
    QUEUE_UNDERFLOW = "ERROR_QUEUE_UNDERFLOW"


@autospec
@dataclass(kw_only=True)
class MessageSyncPayload(ThriftObject):
    items: List[MessageSyncEvent] = field(factory=lambda: [])
    first_seq_id: int = field(TType.I64, default=None)
    last_seq_id: int = field(TType.I64, default=None)
    viewer: int = field(TType.I64, default=None)
    subscribe_ok: str = field(index=11, default=None)
    error: MessageSyncError = field(TType.BINARY, index=12, default=None)
