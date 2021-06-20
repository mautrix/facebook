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
from typing import Optional, List, ClassVar
from abc import ABC

from attr import dataclass
import attr

from mautrix.types import Serializable, SerializableAttrs, SerializableEnum
from ..common import ThreadFolder


class GraphQLQuery(ABC, Serializable):
    caller_class: ClassVar[str] = "graphservice"
    doc_id: ClassVar[int]


class GraphQLMutation(GraphQLQuery, ABC):
    pass


@dataclass
class NTContext(SerializableAttrs):
    styles_id: str = "7d328425a4dfa3aa76b1310fa8dc30bf"
    using_white_navbar: bool = True
    pixel_ratio: int = 3


@dataclass
class ThreadQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 5487678687924830

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
    doc_id: ClassVar[int] = 3562683343826563

    msg_count: int = 20
    thread_count: int = 20
    include_thread_info: str = "true"
    include_message_info: str = "true"
    fetch_users_separately: str = "false"
    filter_to_groups: str = "false"
    include_booking_requests: bool = True

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
    doc_id: ClassVar[int] = 3447218621980314

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
    doc_id: ClassVar[int] = 3090707060965997

    new_thread_name: str
    thread_id: str
    client_mutation_id: str
    actor_id: str
    source: ThreadNameMutationSource = ThreadNameMutationSource.SETTINGS


@dataclass
class FetchStickersWithPreviewsQuery(GraphQLQuery, SerializableAttrs):
    doc_id: ClassVar[int] = 3154119451330002
    caller_class: ClassVar[str] = "com.facebook.messaging.sync.delta.NewMessageHandlerHelper"

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

    message_id: str
    client_mutation_id: str
    actor_id: str


class ReactionAction(SerializableEnum):
    ADD = "ADD_REACTION"
    REMOVE = "REMOVE_REACTION"


@dataclass
class MessageReactionMutation(GraphQLMutation, SerializableAttrs):
    doc_id: ClassVar[int] = 1415891828475683

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
    doc_id: ClassVar[int] = 2948398158550055

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
    doc_id: ClassVar[int] = 3414226858659179

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

    profile_pic_large_size: int = 880
    profile_pic_medium_size: int = 220
    profile_pic_small_size: int = 138
