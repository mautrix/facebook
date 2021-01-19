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
from typing import Optional, Union, List, TYPE_CHECKING, ClassVar
from enum import Enum

from asyncpg import Record
from attr import dataclass

from mautrix.types import RoomID, ContentURI
from mautrix.util.async_db import Database
from maufbapi.types.mqtt import ThreadKey as MQTTThreadKey
from maufbapi.types.graphql import ThreadKey as GraphQLThreadKey

fake_db = Database("") if TYPE_CHECKING else None


class ThreadType(Enum):
    USER = "USER"
    GROUP = "GROUP"
    PAGE = "PAGE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_thread_key(cls, thread_key: Union[MQTTThreadKey, GraphQLThreadKey]) -> 'ThreadType':
        if thread_key.thread_fbid:
            return cls.GROUP
        elif thread_key.other_user_id:
            return cls.USER
        else:
            return cls.UNKNOWN


@dataclass
class Portal:
    db: ClassVar[Database] = fake_db

    fbid: int
    fb_receiver: int
    fb_type: ThreadType
    mxid: Optional[RoomID]
    name: Optional[str]
    photo_id: Optional[str]
    avatar_url: Optional[ContentURI]
    encrypted: bool

    @classmethod
    def _from_row(cls, row: Optional[Record]) -> Optional['Portal']:
        if row is None:
            return None
        data = {**row}
        fb_type = ThreadType(data.pop("fb_type"))
        return cls(**data, fb_type=fb_type)

    @classmethod
    async def get_by_fbid(cls, fbid: int, fb_receiver: int) -> Optional['Portal']:
        q = ("SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted "
             "FROM portal WHERE fbid=$1 AND fb_receiver=$2")
        row = await cls.db.fetchrow(q, fbid, fb_receiver)
        return cls._from_row(row)

    @classmethod
    async def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        q = ("SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted "
             "FROM portal WHERE mxid=$1")
        row = await cls.db.fetchrow(q, mxid)
        return cls._from_row(row)

    @classmethod
    async def get_all_by_receiver(cls, fb_receiver: int) -> List['Portal']:
        q = ("SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted "
             "FROM portal WHERE fb_receiver=$1 AND fb_type='USER'")
        rows = await cls.db.fetch(q, fb_receiver)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def all(cls) -> List['Portal']:
        q = ("SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted "
             "FROM portal")
        rows = await cls.db.fetch(q)
        return [cls._from_row(row) for row in rows]

    async def insert(self) -> None:
        q = ("INSERT INTO portal (fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, "
             "                    encrypted) "
             "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)")
        await self.db.execute(q, self.fbid, self.fb_receiver, self.fb_type.name, self.mxid,
                              self.name, self.photo_id, self.avatar_url, self.encrypted)

    async def delete(self) -> None:
        q = "DELETE FROM portal WHERE fbid=$1 AND fb_receiver=$2"
        await self.db.execute(q, self.fbid, self.fb_receiver)

    async def save(self) -> None:
        await self.db.execute("UPDATE portal SET mxid=$3, name=$4, photo_id=$5, avatar_url=$6,"
                              "                  encrypted=$7 "
                              "WHERE fbid=$1 AND fb_receiver=$2",
                              self.fbid, self.fb_receiver, self.mxid, self.name, self.photo_id,
                              self.avatar_url, self.encrypted)
