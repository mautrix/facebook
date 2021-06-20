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
import base64
import json

from attr import dataclass
import attr

from mautrix.types import SerializableAttrs, SerializableEnum, ExtensibleEnum
from maufbapi.thrift import TType, RecursiveType, ThriftObject, field, autospec
from ..common import MessageUnsendability as Unsendability
from ..graphql import ExtensibleAttachment


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
    # index 11: ???
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
class AudioInfo(ThriftObject):
    # index 1: mysterious boolean (true)
    # index 2: mysterious binary (empty)
    url: str = field(index=3)
    duration_ms: int = field(TType.I32)


@autospec
@dataclass(kw_only=True)
class Attachment(ThriftObject):
    media_id_str: str
    mime_type: str = field(default=None)
    file_name: str = field(default=None)
    media_id: int = field(TType.I64, default=None)
    file_size: int = field(TType.I64, default=None)
    # index 6: ???
    extensible_media: str = field(default=None, index=7)
    # indices 8 and 9: ???
    image_info: ImageInfo = field(default=None, index=10)
    video_info: VideoInfo = field(default=None)
    audio_info: AudioInfo = field(default=None)
    # can contain a dash_manifest key with some XML as the value
    # or fbtype key with a number as value
    extra_metadata: Dict[str, str] = field(factory=lambda: {})

    # index 1007?!: unknown bool

    def parse_extensible(self) -> ExtensibleAttachment:
        if not self.extensible_media:
            raise ValueError("This attachment does not contain an extensible attachment")
        data = json.loads(self.extensible_media)
        raw_media_key = f"extensible_message_attachment:{self.media_id_str}"
        expected_key = base64.b64encode(raw_media_key.encode("utf-8")).decode("utf-8").rstrip("=")
        try:
            media_data = data[expected_key]
        except KeyError:
            media_data = list(data.values())[0]
        return ExtensibleAttachment.deserialize(media_data)


@autospec
@dataclass(kw_only=True)
class Reaction(ThriftObject):
    thread: ThreadKey
    message_id: str
    # index 3: unknown int32 (zero)
    reaction_sender_id: int = field(TType.I64, index=4)
    reaction: str = field(default=None)
    message_sender_id: int = field(TType.I64)
    # index 7: unknown number as string, similar to MessageMetadata's index 3


class MentionType(SerializableEnum):
    PERSON = "p"


@dataclass
class Mention(SerializableAttrs):
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
class ExtendedAddMemberParticipant(ThriftObject):
    addee_user_id: int = field(TType.I64)
    adder_user_id: int = field(TType.I64)
    # index 3: unknown int32 (ex: 0)
    timestamp: int = field(TType.I64, index=4)


@autospec
@dataclass
class ExtendedAddMember(ThriftObject):
    thread: ThreadKey
    users: List[ExtendedAddMemberParticipant]


@autospec
@dataclass
class MessageSyncInnerEvent(ThriftObject):
    reaction: Reaction = field(index=10, default=None)
    extended_add_member: ExtendedAddMember = field(index=42, default=None)
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


class ThreadChangeAction(ExtensibleEnum):
    # action_data:
    #   'thread_icon_url': 'https://www.facebook.com/images/emoji.php/v9/t54/1/16/1f408.png'
    #   'thread_icon': '🐈'
    ICON = "change_thread_icon"

    # action_data:
    #   'should_show_icon': '1'
    #   'theme_color': 'FF5E007E'
    #   'accessibility_label': 'Grape'
    THEME = "change_thread_theme"

    # action_data:
    #   'THREAD_CATEGORY': 'GROUP'
    #   'TARGET_ID': '<user id>'
    #   'ADMIN_TYPE': '0'
    #   'ADMIN_EVENT': 'add_admin' or 'remove_admin'
    ADMINS = "change_thread_admins"

    # action_data:
    #   'APPROVAL_MODE': '1' (or '0'?)
    #   'THREAD_CATEGORY': 'GROUP'
    APPROVAL_MODE = "change_thread_approval_mode"

    # action_data:
    #   'nickname': '<per-room displayname>'
    #   'participant_id': '<user id>'
    NICKNAME = "change_thread_nickname"


@autospec
@dataclass(kw_only=True)
class ThreadChange(ThriftObject):
    metadata: MessageMetadata
    action: ThreadChangeAction = field(TType.BINARY)
    action_data: Dict[str, str] = field(TType.MAP,
                                        key_type=RecursiveType(TType.BINARY, python_type=str),
                                        value_type=RecursiveType(TType.BINARY, python_type=str))


@autospec
@dataclass(kw_only=True)
class AddMemberParticipant(ThriftObject):
    id: int = field(TType.I64)
    first_name: str
    name: str
    # index 4: unknown boolean


@autospec
@dataclass(kw_only=True)
class AddMember(ThriftObject):
    metadata: MessageMetadata
    users: List[AddMemberParticipant]


@autospec
@dataclass(kw_only=True)
class RemoveMember(ThriftObject):
    metadata: MessageMetadata
    user_id: int = field(TType.I64)


@autospec
@dataclass(kw_only=True)
class UnknownReceipt1(ThriftObject):
    thread: ThreadKey
    user_id: int = field(TType.I64)
    # indices 3-5: ???
    message_id_list: List[str] = field(index=6)
    timestamp: int = field(TType.I64)


@autospec
@dataclass(kw_only=True)
class MessageSyncEvent(ThriftObject):
    # index 1: unknown struct (no fields known)
    message: Message = field(index=2, default=None)
    own_read_receipt: OwnReadReceipt = field(index=4, default=None)
    add_member: AddMember = field(index=8, default=None)
    remove_member: RemoveMember = field(index=9, default=None)
    name_change: NameChange = field(index=10, default=None)
    avatar_change: AvatarChange = field(index=11, default=None)
    thread_change: ThreadChange = field(index=17, default=None)
    read_receipt: ReadReceipt = field(index=19, default=None)
    unknown_receipt_1: UnknownReceipt1 = field(index=25, default=None)
    binary: BinaryData = field(index=42, default=None)

    def get_parts(self) -> List[Any]:
        parts = [self.message, self.own_read_receipt, self.add_member, self.remove_member,
                 self.name_change, self.avatar_change, self.thread_change, self.read_receipt,
                 self.unknown_receipt_1]
        if self.binary:
            for inner_item in self.binary.parse().items:
                parts += [inner_item.reaction, inner_item.extended_message,
                          inner_item.unsend_message, inner_item.extended_add_member]
        return [part for part in parts if part is not None]


class MessageSyncError(ExtensibleEnum):
    QUEUE_OVERFLOW = "ERROR_QUEUE_OVERFLOW"
    QUEUE_UNDERFLOW = "ERROR_QUEUE_UNDERFLOW"


@autospec
@dataclass(kw_only=True)
class MessageSyncPayload(ThriftObject):
    items: List[MessageSyncEvent] = field(factory=lambda: [])
    first_seq_id: int = field(TType.I64, default=None)
    last_seq_id: int = field(TType.I64, default=None)
    viewer: int = field(TType.I64, default=None)
    # indices 5-10: ???
    subscribe_ok: str = field(index=11, default=None)
    error: MessageSyncError = field(TType.BINARY, default=None)


@autospec
@dataclass(kw_only=True)
class SendMessageResponse(ThriftObject):
    offline_threading_id: int = field(TType.I64)
    success: bool
    # index 3: unknown i32 present for errors
    error_message: str = field(default=None, index=4)
    # index 5: unknown boolean present for errors


@autospec
@dataclass
class RegionHint(ThriftObject):
    code: str


@autospec
@dataclass(kw_only=True)
class RegionHintPayload(ThriftObject):
    unknown_int64: int = field(TType.I64)
    region_hint_data: bytes

    @property
    def region_hint(self) -> RegionHint:
        return RegionHint.from_thrift(self.region_hint_data)
