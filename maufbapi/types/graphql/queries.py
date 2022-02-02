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
    doc_id: ClassVar[int]


class GraphQLMutation(GraphQLQuery, ABC):
    pass


@dataclass
class NTContext(SerializableAttrs):
    styles_id: str = "632609037bc0e06f1115e7af19c2feae"
    using_white_navbar: bool = True
    pixel_ratio: int = 3
    bloks_version: str = "b1ddbd8ab663f5139265edaa2524450988475f61c5fcd3b06eb7fa9fb1033586"


@dataclass
class ThreadQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 5123202644403703

    thread_ids: List[str]
    msg_count: int = 20

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


@dataclass
class ThreadListQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 4669664453079908

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


@dataclass
class MoreMessagesQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 5045216125512296

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


class ThreadNameMutationSource(SerializableEnum):
    SETTINGS = "SETTINGS"


@dataclass
class ThreadNameMutation(GraphQLMutation, SerializableAttrs):
    doc_id: ClassVar[int] = 4678460715515343

    new_thread_name: str
    thread_id: str
    client_mutation_id: str
    actor_id: str
    source: ThreadNameMutationSource = ThreadNameMutationSource.SETTINGS


@dataclass
class FetchStickersWithPreviewsQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 4028336233932975
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
    doc_id: ClassVar[int] = 1015037405287590
    analytics_tags: ClassVar[List[str]] = _analytics_tags_2

    message_id: str
    client_mutation_id: str
    actor_id: str


class ReactionAction(SerializableEnum):
    ADD = "ADD_REACTION"
    REMOVE = "REMOVE_REACTION"


@dataclass
class MessageReactionMutation(GraphQLMutation, SerializableAttrs):
    doc_id: ClassVar[int] = 4581961245172668
    analytics_tags: ClassVar[List[str]] = _analytics_tags_2

    message_id: str
    client_mutation_id: str
    actor_id: str
    action: ReactionAction
    reaction: Optional[str] = None


@dataclass
class DownloadImageFragment(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 3063616537053520

    fbid: str
    img_size: str = "0"


@dataclass
class FbIdToCursorQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 2015407048575350

    fbid: str
    thread_id: str


@dataclass
class SubsequentMediaQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 4376490155778570

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
    doc_id: ClassVar[int] = 3200288700012393

    thread_msg_id: ThreadMessageID


@dataclass
class SearchEntitiesNamedQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 4883329948399448

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
class UpdateThreadCopresence(GraphQLMutation, SerializableAttrs):
    doc_id: ClassVar[int] = 3020568468070941
    analytics_tags: ClassVar[List[str]] = _analytics_tags_2

    thread_key: str
    presence_state: str = "IN_THREAD"
    capabilities: int = 1
