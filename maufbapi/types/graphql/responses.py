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
from typing import Optional, List, Dict, Tuple, Union

from yarl import URL
from attr import dataclass
import attr

from mautrix.types import ExtensibleEnum, SerializableAttrs, JSON, Obj, deserializer
from ..common import ThreadFolder, MessageUnsendability


@dataclass
class ParticipantID(SerializableAttrs):
    id: str


@dataclass
class ReadReceipt(SerializableAttrs):
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
class ReadReceiptList(SerializableAttrs):
    nodes: List[ReadReceipt]


@dataclass
class Picture(SerializableAttrs):
    uri: str
    height: Optional[int] = None
    width: Optional[int] = None

    @property
    def dimensions(self) -> Tuple[int, int]:
        return self.width, self.height


class StructuredNamePart(ExtensibleEnum):
    FIRST = "first"
    MIDDLE = "middle"
    LAST = "last"


@dataclass
class StructuredNameChunk(SerializableAttrs):
    length: int
    offset: int
    part: StructuredNamePart


@dataclass
class StructuredName(SerializableAttrs):
    parts: List[StructuredNameChunk]
    phonetic_name: Optional[str]
    text: str

    def to_dict(self) -> Dict[str, str]:
        return {
            f"{part.part.value}_name": self.text[part.offset:part.offset + part.length]
            for part in self.parts
        }


@dataclass
class FriendCount(SerializableAttrs):
    count: int


@dataclass(kw_only=True)
class MinimalParticipant(ParticipantID, SerializableAttrs):
    name: Optional[str] = None


class FriendshipStatus(ExtensibleEnum):
    ARE_FRIENDS = "ARE_FRIENDS"
    CAN_REQUEST = "CAN_REQUEST"
    CANNOT_REQUEST = "CANNOT_REQUEST"
    INCOMING_REQUEST = "INCOMING_REQUEST"
    OUTGOING_REQUEST = "OUTGOING_REQUEST"


class ReachabilityStatus(ExtensibleEnum):
    REACHABLE = "REACHABLE"
    UNREACHABLE_USER_TYPE = "UNREACHABLE_USER_TYPE"


class ParticipantType(ExtensibleEnum):
    USER = "User"
    PAGE = "Page"


@dataclass(kw_only=True)
class Participant(MinimalParticipant, SerializableAttrs):
    typename: ParticipantType = attr.ib(metadata={"json": "__typename"})

    username: Optional[str] = None
    structured_name: Optional[StructuredName] = None
    nickname_for_viewer: Optional[str] = None

    profile_pic_small: Optional[Picture] = None
    profile_pic_medium: Optional[Picture] = None
    profile_pic_large: Optional[Picture] = None

    friends: Optional[FriendCount] = None
    friendship_status: Optional[FriendshipStatus] = None
    mutual_friends: Optional[FriendCount] = None
    reachability_status_type: Optional[ReachabilityStatus] = None
    registration_time: Optional[int] = None

    is_aloha_proxy_confirmed: bool = False
    is_blocked_by_viewer: bool = False
    is_banned_by_page_viewer: bool = False
    is_deactivated_allowed_on_messenger: bool = False
    is_managing_parent_approved_user: bool = False
    is_memorialized: bool = False
    is_message_blocked_by_viewer: bool = False
    is_message_ignored_by_viewer: bool = False
    is_pseudo_blocked_by_viewer: bool = False
    is_messenger_user: bool = False
    is_partial: Optional[bool] = None
    is_verified: bool = False
    is_viewer_friend: bool = False
    can_viewer_message: bool = True


@dataclass
class ParticipantNode(SerializableAttrs):
    id: str
    messaging_actor: Participant


@dataclass
class ParticipantList(SerializableAttrs):
    nodes: List[ParticipantNode]


@dataclass
class MessageSender(SerializableAttrs):
    id: str
    messaging_actor: MinimalParticipant


@dataclass
class MessageRange(SerializableAttrs):
    length: int
    offset: int
    entity: Optional[ParticipantID] = None

    @property
    def user_id(self) -> str:
        return self.entity.id


class MessagePowerUpType(ExtensibleEnum):
    NONE = "NONE"
    LOVE = "LOVE"
    GIFT_WRAP = "GIFT_WRAP"
    CELEBRATION = "CELEBRATION"
    FIRE = "FIRE"


@dataclass
class MessagePowerUp(SerializableAttrs):
    style: MessagePowerUpType


@dataclass
class MessageText(SerializableAttrs):
    text: str
    ranges: List[MessageRange] = attr.ib(factory=lambda: [])


@dataclass
class Reaction(SerializableAttrs):
    reaction: str
    reaction_timestamp: int
    user: ParticipantID


@dataclass
class Dimensions(SerializableAttrs):
    x: int
    y: int


class AttachmentType(ExtensibleEnum):
    IMAGE = "MessageImage"
    ANIMATED_IMAGE = "MessageAnimatedImage"
    FILE = "MessageFile"
    AUDIO = "MessageAudio"
    VIDEO = "MessageVideo"
    LOCATION = "MessageLocation"
    LIVE_LOCATION = "MessageLiveLocation"
    EXTERNAL_URL = "ExternalUrl"
    STORY = "Story"


class ImageType(ExtensibleEnum):
    FILE_ATTACHMENT = "FILE_ATTACHMENT"
    MESSENGER_CAM = "MESSENGER_CAM"
    TRANSPARENT = "TRANSPARENT"


class VideoType(ExtensibleEnum):
    FILE_ATTACHMENT = "FILE_ATTACHMENT"
    RECORDED_VIDEO = "RECORDED_VIDEO"
    SPEAKING_STICKER = "SPEAKING_STICKER"
    RECORDED_STICKER = "RECORDED_STICKER"
    VIDEO_MAIL = "VIDEO_MAIL"
    IG_SELFIE_STICKER = "IG_SELFIE_STICKER"


@dataclass
class Attachment(SerializableAttrs):
    typename: AttachmentType = attr.ib(metadata={"json": "__typename"})
    id: str
    attachment_fbid: str
    filename: str
    mimetype: str
    filesize: Optional[int] = None
    render_as_sticker: bool = False

    image_type: Optional[ImageType] = None
    original_dimensions: Optional[Dimensions] = None
    image_blurred_preview: Optional[Picture] = None
    image_full_screen: Optional[Picture] = None
    image_large_preview: Optional[Picture] = None
    image_medium_preview: Optional[Picture] = None
    image_small_preview: Optional[Picture] = None

    # For animated images
    animated_image_render_as_sticker: bool = False
    animated_image_original_dimensions: Optional[Dimensions] = None
    animated_image_full_screen: Optional[Picture] = None
    animated_image_large_preview: Optional[Picture] = None
    animated_image_medium_preview: Optional[Picture] = None
    animated_image_small_preview: Optional[Picture] = None
    animated_static_image_full_screen: Optional[Picture] = None
    animated_static_image_large_preview: Optional[Picture] = None
    animated_static_image_medium_preview: Optional[Picture] = None
    animated_static_image_small_preview: Optional[Picture] = None

    # For audio files
    is_voicemail: bool = False
    playable_url: Optional[str] = None

    # For audio and video files
    playable_duration_in_ms: Optional[int] = None

    # For video files
    video_type: Optional[VideoType] = None
    streaming_image_thumbnail: Optional[Picture] = attr.ib(default=None, metadata={
        "json": "streamingImageThumbnail"})
    video_filesize: Optional[int] = None
    attachment_video_url: Optional[str] = None


@dataclass
class MinimalSticker(SerializableAttrs):
    # 369239263222822 = "like"
    id: str


@dataclass
class StickerPackMeta(SerializableAttrs):
    id: str
    is_comments_capable: bool
    is_composer_capable: bool
    is_messenger_capable: bool
    is_messenger_kids_capable: bool
    is_montage_capable: bool
    is_posts_capable: bool
    is_sms_capable: bool


class StickerType(ExtensibleEnum):
    REGULAR = "REGULAR"
    AVATAR = "AVATAR"
    CUSTOM = "CUSTOM"


@dataclass
class Sticker(MinimalSticker, SerializableAttrs):
    pack: StickerPackMeta
    animated_image: Picture
    preview_image: Picture
    thread_image: Picture
    sticker_type: StickerType
    label: Optional[str] = None


@dataclass
class ExtensibleText(SerializableAttrs):
    text: str


@dataclass
class Coordinates(SerializableAttrs):
    latitude: float
    longitude: float


@dataclass
class StoryTarget(SerializableAttrs):
    typename: AttachmentType = attr.ib(metadata={"json": "__typename"})
    id: Optional[str] = None
    url: Optional[str] = None
    coordinates: Optional[Coordinates] = None


@dataclass
class StoryMediaAttachment(SerializableAttrs):
    typename_str: str = attr.ib(metadata={"json": "__typename"})
    id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    owner: Optional[MinimalParticipant] = None
    title: Optional[ExtensibleText] = None
    image: Optional[Picture] = None
    image_natural: Optional[Picture] = attr.ib(default=None, metadata={"json": "imageNatural"})
    image_fullscreen: Optional[Picture] = attr.ib(default=None,
                                                  metadata={"json": "imageFullScreen"})
    image_large: Optional[Picture] = attr.ib(default=None, metadata={"json": "imageLarge"})
    playable_url: Optional[str] = None
    is_looping: bool = False
    is_playable: bool = False


@dataclass
class StoryAttachment(SerializableAttrs):
    title: str
    url: str
    # TODO enum? share, message_location, attached_story, photo, games_app, messenger_native_templates, unavailable, fallback
    style_list: List[str] = attr.ib(factory=lambda: [])
    title_with_entities: Optional[ExtensibleText] = None
    description: Optional[ExtensibleText] = None
    source: Optional[ExtensibleText] = None
    subtitle: Optional[str] = None
    target: Optional[StoryTarget] = None
    deduplication_key: Optional[str] = None
    media: Optional[StoryMediaAttachment] = None

    @property
    def clean_url(self) -> URL:
        url = URL(self.url)
        if url.host == "l.facebook.com":
            url = URL(url.query["u"])
        elif url.scheme == "fbrpc" and url.host == "facebook" and url.path == "/nativethirdparty":
            url = URL(url.query["target_url"])
        return url


@dataclass
class ExtensibleAttachment(SerializableAttrs):
    id: str
    is_forwardable: bool
    story_attachment: Optional[StoryAttachment] = None


@dataclass(kw_only=True)
class MinimalMessage(SerializableAttrs):
    # IDs and message are not present in some action messages like adding to group
    id: Optional[str] = None
    message_id: Optional[str] = None
    message: Optional[MessageText] = None
    message_sender: MessageSender
    sticker: Optional[MinimalSticker] = None
    blob_attachments: List[Attachment] = attr.ib(factory=lambda: [])
    extensible_attachment: ExtensibleAttachment = attr.ib(default=None)


class ReplyStatus(ExtensibleEnum):
    VALID = "VALID"
    DELETED = "DELETED"


@dataclass
class Reply(SerializableAttrs):
    message: MinimalMessage
    status: ReplyStatus


@dataclass(kw_only=True)
class Message(MinimalMessage, SerializableAttrs):
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
class PageInfo(SerializableAttrs):
    has_next_page: bool = False
    has_previous_page: bool = False

    end_cursor: Optional[str] = None
    start_cursor: Optional[str] = None


@dataclass
class MessageList(SerializableAttrs):
    nodes: List[Message]
    # Not present in last_message and other such "lists"
    page_info: PageInfo = attr.ib(factory=lambda: PageInfo())


@dataclass(eq=True, frozen=True)
class ThreadKey(SerializableAttrs):
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
class ThreadParticipantCustomization(SerializableAttrs):
    participant_id: str
    nickname: str = ""


@dataclass(kw_only=True)
class ThreadCustomizationInfo(SerializableAttrs):
    custom_like_emoji: Optional[str] = None
    participant_customizations: List[ThreadParticipantCustomization] = attr.ib(factory=lambda: [])

    @property
    def nickname_map(self) -> Dict[int, str]:
        return {int(pc.participant_id): pc.nickname for pc in self.participant_customizations}


@dataclass(kw_only=True)
class Thread(SerializableAttrs):
    id: str
    folder: ThreadFolder
    name: Optional[str]
    thread_key: ThreadKey
    image: Optional[Picture]

    messages_count: int
    unread_count: int
    unsend_limit: int
    mute_until: Optional[int]
    privacy_mode: int
    thread_pin_timestamp: int
    thread_queue_enabled: bool
    thread_unsendability_status: MessageUnsendability
    update_time_precise: Optional[str] = None

    last_message: MessageList
    messages: MessageList
    read_receipts: ReadReceiptList
    all_participants: ParticipantList
    customization_info: ThreadCustomizationInfo

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
class ThreadListResponse(SerializableAttrs):
    count: int
    unread_count: int
    unseen_count: int
    mute_until: int
    nodes: List[Thread]
    page_info: PageInfo
    sync_sequence_id: str


@dataclass
class ThreadQueryResponse(SerializableAttrs):
    message_threads: List[Thread]


@dataclass
class StickerPreviewResponse(SerializableAttrs):
    nodes: List[Sticker]


@dataclass
class MessageUnsendResponse(SerializableAttrs):
    did_succeed: bool
    error_code: str
    error_message: str


@dataclass
class ImageFragment(SerializableAttrs):
    typename: AttachmentType = attr.ib(metadata={"json": "__typename"})
    id: str
    animated_gif: Optional[Picture] = None
    image: Optional[Picture] = None


@dataclass
class SubsequentMediaNode(SerializableAttrs):
    typename: AttachmentType = attr.ib(metadata={"json": "__typename"})
    id: str
    legacy_attachment_id: str
    creation_time: int
    creator: MinimalParticipant
    adjusted_size: Optional[Picture] = None
    image_thumbnail: Optional[Picture] = attr.ib(default=None, metadata={"json": "imageThumbnail"})
    media_url: Optional[str] = attr.ib(default=None, metadata={"json": "mediaUrl"})
    original_dimensions: Optional[Dimensions] = None


@dataclass
class SubsequentMediaResponse(SerializableAttrs):
    nodes: List[SubsequentMediaNode]
    page_info: PageInfo


@dataclass
class FileAttachmentWithURL(SerializableAttrs):
    typename: AttachmentType = attr.ib(metadata={"json": "__typename"})
    attachment_fbid: str
    id: str
    url: str


@dataclass
class FileAttachmentURLResponse(SerializableAttrs):
    id: str
    blob_attachments: List[FileAttachmentWithURL]


@dataclass(kw_only=True)
class OwnInfo(SerializableAttrs):
    id: str
    birthday: Optional[str] = None
    gender: Optional[str] = None
    locale: Optional[str] = None
    email: Optional[str] = None
    name: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    link: Optional[str] = None
    is_employee: bool = False
    verified: bool = False
    published_timeline: bool = False
    timezone: int = 0
    updated_time: Optional[str] = None


@dataclass
class MessageSearchResult(SerializableAttrs):
    thread_id: str
    name: Optional[str]
    # TODO message_thread, matched_message


# TODO there might be other types of search results
SearchResultNode = Union[MessageSearchResult, Participant, Obj]


@deserializer(SearchResultNode)
def deserialize_search_node(val: JSON) -> SearchResultNode:
    type = val["__typename"]
    if type in ("User", "Page"):
        return Participant.deserialize(val)
    elif type == "MessageSearchResult":
        return MessageSearchResult.deserialize(val)
    return Obj(**val)


@dataclass
class SearchResult(SerializableAttrs):
    node: SearchResultNode
    # there's a header field with some random metadata too


@dataclass
class SearchResults(SerializableAttrs):
    edges: List[SearchResult]


@dataclass
class SearchEntitiesResponse(SerializableAttrs):
    cache_id: str
    search_results: SearchResults
