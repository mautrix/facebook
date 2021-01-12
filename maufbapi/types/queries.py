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

from mautrix.types import Serializable, SerializableAttrs
from attr import dataclass
import attr


class GraphQLQuery(ABC, Serializable):
    doc_id: ClassVar[int]


@dataclass
class NTContext(SerializableAttrs['ThreadListQueryNTContext']):
    styles_id: str = "7d328425a4dfa3aa76b1310fa8dc30bf"
    using_white_navbar: bool = True
    pixel_ratio: int = 3


@dataclass
class ThreadListQuery(GraphQLQuery, SerializableAttrs['ThreadListQuery']):
    doc_id: ClassVar[int] = 3562683343826563

    msg_count: int = 20
    thread_count: int = 20
    include_thread_info: str = "true"
    include_message_info: str = "true"
    fetch_users_separately: str = "false"
    filter_to_groups: str = "false"
    include_booking_requests: bool = True

    nt_context: NTContext = attr.ib(factory=lambda: NTContext())
    folder_tag: Optional[List[str]] = None

    theme_icon_size_small: int = 66
    reaction_static_asset_size_small: int = 39
    profile_pic_medium_size: int = 220
    profile_pic_large_size: int = 880
    profile_pic_small_size: int = 138
    theme_background_size: int = 2048
    theme_icon_size_large: int = 138


@dataclass
class MoreMessagesQuery(GraphQLQuery, SerializableAttrs['MoreMessagesQuery']):
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
