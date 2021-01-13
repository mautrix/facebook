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

from ..thrift import TType, RecursiveType, field, autospec
from .responses import MessageUnsendability


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
    # index 3: unknown int64, example: 6754745637852805729
    sender: int = field(TType.I64, index=4)
    timestamp: int = field(TType.I64)
    # index 6: unknown bool
    # index 7: ???
    tags: List[str] = field(index=8, factory=lambda: [])
    # index 9: unknown int32
    # index 10: unknown bool
    message_unsendability_status: MessageUnsendability = field(TType.BINARY, index=12)
    # indices 13-16: ???
    # index 17: struct -> set at index 2 of int64 user ids


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
class MessageSyncData:
    metadata: MessageMetadata
    text: str
    # indices 3 and 4: ???
    attachments: List[Attachment] = field(index=5, factory=lambda: [])
    # index 6: some sort of struct:
    #    1: List[BinaryThreadKey]?
    #    2: ???
    #    3: timestamp?
    #    4: timestamp?


@autospec
@dataclass(kw_only=True)
class BinaryData:
    data: bytes


@autospec
@dataclass(kw_only=True)
class MessageSyncEvent:
    data: MessageSyncData = field(index=2, default=None)
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
