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
from typing import Optional, List, Dict

from attr import dataclass
import attr

from mautrix.types import SerializableEnum, SerializableAttrs
from ..common import ThreadFolder, MessageUnsendability


@dataclass
class ParticipantID(SerializableAttrs['ParticipantID']):
    id: str


@dataclass
class ReadReceipt(SerializableAttrs['ReadReceipt']):
    action_timestamp_precise: str
    timestamp_precise: str
    actor: ParticipantID

    @property
    def timestamp(self) -> int:
        return int(self.timestamp_precise)

    @property
    def action_timestamp(self) -> int:
        return int(self.action_timestamp_precise)


@dataclass
class ReadReceiptList(SerializableAttrs['ReadReceiptList']):
    nodes: List[ReadReceipt]


@dataclass
class Picture(SerializableAttrs['Picture']):
    uri: str
    height: Optional[int] = None


@dataclass
class StructuredNameChunk(SerializableAttrs['StructuredNameChunk']):
    length: int
    offset: int
    # TODO enum? first/middle/last
    part: str


@dataclass
class StructuredName(SerializableAttrs['StructuredName']):
    parts: List[StructuredNameChunk]
    phonetic_name: Optional[str]
    text: str

    def to_dict(self) -> Dict[str, str]:
        return {
            f"{part.part}_name": self.text[part.offset:part.offset + part.length]
            for part in self.parts
        }


@dataclass
class FriendCount(SerializableAttrs['FriendCount']):
    count: int


@dataclass(kw_only=True)
class MinimalParticipant(ParticipantID, SerializableAttrs['MinimalParticipant']):
    name: Optional[str] = None


@dataclass(kw_only=True)
class Participant(MinimalParticipant, SerializableAttrs['Participant']):
    structured_name: StructuredName
    username: str
    nickname_for_viewer: Optional[str]

    profile_pic_small: Optional[Picture]
    profile_pic_medium: Optional[Picture]
    profile_pic_large: Optional[Picture]

    friends: FriendCount
    # TODO enum? CAN/CANNOT_REQUEST
    friendship_status: str
    mutual_friends: FriendCount
    # TODO enum? REACHABLE
    reachability_status_type: str
    registration_time: int

    is_aloha_proxy_confirmed: bool
    is_blocked_by_viewer: bool
    is_banned_by_page_viewer: bool
    is_deactivated_allowed_on_messenger: bool
    is_managing_parent_approved_user: bool
    is_memorialized: bool
    is_message_blocked_by_viewer: bool
    is_message_ignored_by_viewer: bool
    is_pseudo_blocked_by_viewer: bool
    is_messenger_user: bool
    is_partial: bool
    is_verified: bool
    is_viewer_friend: bool
    can_viewer_message: bool


@dataclass
class ParticipantNode(SerializableAttrs['ParticipantNode']):
    id: str
    messaging_actor: Participant


@dataclass
class ParticipantList(SerializableAttrs['ParticipantList']):
    nodes: List[ParticipantNode]


@dataclass
class MessageSender(SerializableAttrs['MessageSender']):
    id: str
    messaging_actor: MinimalParticipant


@dataclass
class MessageRange(SerializableAttrs['MessageRange']):
    length: int
    offset: int
    entity: Optional[ParticipantID] = None

    @property
    def user_id(self) -> str:
        return self.entity.id


@dataclass
class MessagePowerUp(SerializableAttrs['MessagePowerUp']):
    # TODO enum? NONE
    style: str


@dataclass
class MessageText(SerializableAttrs['MessageText']):
    text: str
    ranges: List[MessageRange] = attr.ib(factory=lambda: [])


@dataclass
class Reaction(SerializableAttrs['Reaction']):
    reaction: str
    reaction_timestamp: int
    user: ParticipantID


@dataclass
class Dimensions(SerializableAttrs['Dimensions']):
    x: int
    y: int


@dataclass
class Attachment(SerializableAttrs['Attachment']):
    # TODO enum? MessageImage or MessageFile
    typename: str = attr.ib(metadata={"json": "__typename"})
    id: str
    attachment_fbid: str
    filename: str
    filesize: int
    mimetype: str

    # TODO enum? FILE_ATTACHMENT
    image_type: Optional[str] = None
    original_dimensions: Optional[Dimensions] = None
    render_as_sticker: bool = False
    image_blurred_preview: Optional[Picture] = None
    image_full_screen: Optional[Picture] = None
    image_large_preview: Optional[Picture] = None
    image_medium_preview: Optional[Picture] = None
    image_small_preview: Optional[Picture] = None


@dataclass
class MinimalSticker(SerializableAttrs['MinimalSticker']):
    # 369239263222822 = "like"
    id: str


@dataclass
class StickerPackMeta(SerializableAttrs['StickerPackMeta']):
    id: str
    is_comments_capable: bool
    is_composer_capable: bool
    is_messenger_capable: bool
    is_messenger_kids_capable: bool
    is_montage_capable: bool
    is_posts_capable: bool
    is_sms_capable: bool


@dataclass
class Sticker(MinimalSticker, SerializableAttrs['Sticker']):
    pack: StickerPackMeta
    animated_image: Picture
    preview_image: Picture
    thread_image: Picture
    # TODO enum? REGULAR
    sticker_type: str
    label: Optional[str] = None


@dataclass(kw_only=True)
class MinimalMessage(SerializableAttrs['MinimalMessage']):
    # IDs and message are not present in some action messages like adding to group
    id: Optional[str] = None
    message_id: Optional[str] = None
    message: Optional[MessageText] = None
    message_sender: MessageSender
    sticker: Optional[MinimalSticker] = None
    blob_attachments: List[Attachment] = attr.ib(factory=lambda: [])


class ReplyStatus(SerializableEnum):
    VALID = "VALID"


@dataclass
class Reply(SerializableAttrs['Reply']):
    message: MinimalMessage
    status: ReplyStatus


@dataclass(kw_only=True)
class Message(MinimalMessage, SerializableAttrs['Message']):
    snippet: str
    timestamp_precise: str
    unsent_timestamp_precise: Optional[str] = None
    offline_threading_id: Optional[str] = None
    tags_list: List[str] = attr.ib(factory=lambda: [])
    message_reactions: List[Reaction] = attr.ib(factory=lambda: [])
    replied_to_message: Optional[Reply] = None
    message_unsendability_status: MessageUnsendability = MessageUnsendability.CAN_UNSEND

    is_sponsored: bool = False
    is_user_generated: bool = True
    unread: bool = False
    ttl: Optional[int] = None

    @property
    def timestamp(self) -> int:
        return int(self.timestamp_precise)

    @property
    def unsent_timestamp(self) -> Optional[int]:
        return int(self.unsent_timestamp_precise) if self.unsent_timestamp_precise else None


@dataclass
class PageInfo(SerializableAttrs['PageInfo']):
    has_next_page: bool = False
    has_previous_page: bool = False


@dataclass
class MessageList(SerializableAttrs['MessageList']):
    nodes: List[Message]
    # Not present in last_message and other such "lists"
    page_info: PageInfo = attr.ib(factory=lambda: PageInfo())


@dataclass
class ThreadKey(SerializableAttrs['ThreadKey']):
    other_user_id: Optional[str] = None
    thread_fbid: Optional[str] = None

    @property
    def id(self) -> Optional[int]:
        if self.other_user_id:
            return int(self.other_user_id)
        elif self.thread_fbid:
            return int(self.thread_fbid)
        else:
            return None


@dataclass(kw_only=True)
class Thread(SerializableAttrs['Thread']):
    id: str
    folder: ThreadFolder
    name: Optional[str]
    thread_key: ThreadKey
    image: Optional[Picture]

    messages_count: int
    unread_count: int
    unsend_limit: int
    privacy_mode: int
    thread_pin_timestamp: int
    thread_queue_enabled: bool
    thread_unsendability_status: MessageUnsendability
    update_time_precise: Optional[str] = None

    last_message: MessageList
    messages: MessageList
    read_receipts: ReadReceiptList
    all_participants: ParticipantList

    thread_admins: List[ParticipantID]

    is_admin_supported: bool
    is_business_page_active: bool
    is_disappearing_mode: bool
    is_fuss_red_page: bool
    is_group_thread: bool
    is_ignored_by_viewer: bool
    is_pinned: bool
    is_viewer_allowed_to_add_members: bool
    is_viewer_subscribed: bool
    can_viewer_reply: bool
    can_participants_claim_admin: bool


@dataclass
class ThreadListResponse(SerializableAttrs['ThreadListResponse']):
    count: int
    unread_count: int
    unseen_count: int
    mute_until: int
    nodes: List[Thread]
    page_info: PageInfo
    sync_sequence_id: str


@dataclass
class StickerPreviewResponse(SerializableAttrs['StickerPreviewResponse']):
    nodes: List[Sticker]


@dataclass
class MessageUnsendResponse(SerializableAttrs['MessageUnsendResponse']):
    did_succeed: bool
    error_code: str
    error_message: str
