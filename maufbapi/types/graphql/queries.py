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
from typing import ClassVar, List, Optional
from abc import ABC

from attr import dataclass
import attr

from mautrix.types import Serializable, SerializableAttrs, SerializableEnum

from ..common import ThreadFolder

_analytics_tags_2 = ["nav_attribution_id=null", "visitation_id=null", "GraphServices"]


class GraphQLQuery(ABC, Serializable):
    caller_class: ClassVar[str] = "graphservice"
    analytics_tags: ClassVar[List[str]] = ["GraphServices"]
    include_client_country_code: ClassVar[bool] = False
    client_doc_id: ClassVar[int] = None
    doc_id: ClassVar[Optional[int]] = None


class GraphQLMutation(GraphQLQuery, ABC):
    pass


@dataclass
class NTContext(SerializableAttrs):
    styles_id: str = "75ceb8745bb35e5b9592e8c0f326ff93"
    using_white_navbar: bool = True
    pixel_ratio: int = 3
    bloks_version: str = "767fdd9f0b95fc47193a332ff7e90f48e86e3d63fd412b2414fc5528dda2feb8"
    is_push_on: bool = True


@dataclass
class ThreadQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 261428482816184255718129058618

    thread_ids: List[str]
    msg_count: int = 20  # 100

    blur: int = 0

    nt_context: NTContext = attr.ib(factory=lambda: NTContext())
    include_full_user_info: str = "true"
    include_message_info: str = "true"
    include_booking_requests: bool = True

    full_screen_width: int = 4096
    full_screen_height: int = 4096
    large_preview_width: int = 1500
    large_preview_height: int = 750
    medium_preview_width: int = 962
    medium_preview_height: int = 481
    small_preview_width: int = 716
    small_preview_height: int = 358
    profile_pic_large_size: int = 880
    profile_pic_small_size: int = 138
    scale: str = "3"


@dataclass
class ThreadListQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 42489038484109023297878016725

    msg_count: int = 20
    thread_count: int = 20
    include_thread_info: str = "true"
    include_message_info: str = "true"
    fetch_users_separately: str = "false"
    filter_to_groups: str = "false"
    include_booking_requests: bool = True
    include_user_message_capabilities: bool = True
    slim_thread_list: bool = True

    nt_context: NTContext = attr.ib(factory=lambda: NTContext())
    folder_tag: Optional[List[ThreadFolder]] = None

    theme_icon_size_small: int = 66
    reaction_static_asset_size_small: int = 39
    profile_pic_medium_size: int = 220
    profile_pic_large_size: int = 880
    profile_pic_small_size: int = 138
    theme_background_size: int = 2048
    theme_icon_size_large: int = 138
    scale: str = "3"


@dataclass
class MoreThreadsQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 101371284010298159468949651434

    after_time_ms: str

    msg_count: int = 20
    thread_count: int = 20
    include_full_user_info: str = "true"
    fetch_users_separately: str = "false"
    filter_to_groups: str = "false"
    include_message_info: str = "true"

    profile_pic_medium_size: int = 220
    profile_pic_large_size: int = 880
    profile_pic_small_size: int = 138
    scale: str = "3"

    nt_context: NTContext = attr.ib(factory=lambda: NTContext())


@dataclass
class MoreMessagesQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 26340105474757789559325557719

    before_time_ms: str
    thread_id: str
    msg_count: int = 20
    blur: int = 0

    nt_context: NTContext = attr.ib(factory=lambda: NTContext())

    full_screen_width: int = 4096
    full_screen_height: int = 4096
    large_preview_width: int = 1500
    large_preview_height: int = 750
    medium_preview_width: int = 962
    medium_preview_height: int = 481
    small_preview_width: int = 716
    small_preview_height: int = 358
    scale: str = "3"


class ThreadNameMutationSource(SerializableEnum):
    SETTINGS = "SETTINGS"


@dataclass
class ThreadNameMutation(GraphQLMutation, SerializableAttrs):
    client_doc_id: ClassVar[int] = 245687281615266693919061310962

    new_thread_name: str
    thread_id: str
    client_mutation_id: str
    actor_id: str
    source: ThreadNameMutationSource = ThreadNameMutationSource.SETTINGS


@dataclass
class FetchStickersWithPreviewsQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 401999615114001344821502923121
    caller_class: ClassVar[str] = "NewMessageHandlerHelper"
    include_client_country_code: ClassVar[bool] = True

    sticker_ids: List[str]
    preview_size: int = 165
    animated_media_type: str = "image/webp"
    media_type: str = "image/webp"
    scaling_factor: str = "2.75"
    sticker_labels_enabled: bool = False
    sticker_state_enabled: bool = False


@dataclass
class MessageUndoSend(GraphQLMutation, SerializableAttrs):
    client_doc_id: ClassVar[int] = 651600610198736392087694690
    analytics_tags: ClassVar[List[str]] = _analytics_tags_2

    message_id: str
    client_mutation_id: str
    actor_id: str


class ReactionAction(SerializableEnum):
    ADD = "ADD_REACTION"
    REMOVE = "REMOVE_REACTION"


@dataclass
class MessageReactionMutation(GraphQLMutation, SerializableAttrs):
    client_doc_id: ClassVar[int] = 3059927964988005121571400082
    analytics_tags: ClassVar[List[str]] = _analytics_tags_2

    message_id: str
    client_mutation_id: str
    actor_id: str
    action: ReactionAction
    reaction: Optional[str] = None


@dataclass
class DownloadImageFragment(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 2226154139486245861670820706

    fbid: str
    img_size: str = "0"


@dataclass
class FbIdToCursorQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 2985653443947768641342601552

    fbid: str
    thread_id: str


@dataclass
class SubsequentMediaQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 42018352655156159811465888541

    thread_id: str
    cursor_id: Optional[str] = None
    fetch_size: int = 99
    thumbnail_size: int = 540
    height: int = 2088
    width: int = 1080


@dataclass(frozen=True, eq=True)
class ThreadMessageID(SerializableAttrs):
    thread_id: str
    message_id: str


@dataclass
class FileAttachmentUrlQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 172089622313141855796265222507

    thread_msg_id: ThreadMessageID


@dataclass
class SearchEntitiesNamedQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 412264286114708880472128431895

    search_query: str
    session_id: Optional[str] = None

    results_limit: int = 20
    num_users_query: int = 20
    num_group_threads_query: int = 20
    num_pages_query: int = 6

    unified_config: str = "DOUBLE_SERVER_QUERY_PRIMARY"
    search_surface: str = "UNIVERSAL_ALL"
    user_types: List[str] = ["CONTACT", "NON_FRIEND_NON_CONTACT"]
    entity_types: List[str] = ["user", "group_thread", "page", "game", "matched_message_thread"]
    include_pages: bool = True
    include_games: bool = True
    group_participants: List[str] = attr.ib(factory=lambda: [])

    profile_pic_large_size: int = 880
    profile_pic_medium_size: int = 220
    profile_pic_small_size: int = 138


@dataclass
class UsersQuery(GraphQLQuery, SerializableAttrs):
    client_doc_id: ClassVar[int] = 12889174649061980331350648993

    user_fbids: List[str]

    profile_pic_large_size: int = 880
    profile_pic_medium_size: int = 220
    profile_pic_small_size: int = 138
