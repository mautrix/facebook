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
from typing import Optional, List, TYPE_CHECKING, ClassVar

from asyncpg import Record
from attr import dataclass
from yarl import URL

from mautrix.types import UserID, SyncToken, ContentURI
from mautrix.util.async_db import Database

fake_db = Database("") if TYPE_CHECKING else None


@dataclass
class Puppet:
    db: ClassVar[Database] = fake_db

    fbid: int
    name: Optional[str]
    photo_id: Optional[str]
    photo_mxc: Optional[ContentURI]
    name_set: bool
    avatar_set: bool
    is_registered: bool

    custom_mxid: Optional[UserID]
    access_token: Optional[str]
    next_batch: Optional[SyncToken]
    base_url: Optional[URL]

    @classmethod
    def _from_row(cls, row: Optional[Record]) -> Optional['Puppet']:
        if row is None:
            return None
        data = {**row}
        base_url = data.pop("base_url", None)
        return cls(**data, base_url=URL(base_url) if base_url else None)

    @classmethod
    async def get_by_fbid(cls, fbid: int) -> Optional['Puppet']:
        q = ("SELECT fbid, name, photo_id, photo_mxc, name_set, avatar_set, is_registered, "
             "       custom_mxid, access_token, next_batch, base_url "
             "FROM puppet WHERE fbid=$1")
        row = await cls.db.fetchrow(q, fbid)
        return cls._from_row(row)

    @classmethod
    async def get_by_name(cls, name: str) -> Optional['Puppet']:
        q = ("SELECT fbid, name, photo_id, photo_mxc, name_set, avatar_set, is_registered, "
             "       custom_mxid, access_token, next_batch, base_url "
             "FROM puppet WHERE name=$1")
        row = await cls.db.fetchrow(q, name)
        return cls._from_row(row)

    @classmethod
    async def get_by_custom_mxid(cls, mxid: UserID) -> Optional['Puppet']:
        q = ("SELECT fbid, name, photo_id, photo_mxc, name_set, avatar_set, is_registered, "
             "       custom_mxid, access_token, next_batch, base_url "
             "FROM puppet WHERE custom_mxid=$1")
        row = await cls.db.fetchrow(q, mxid)
        return cls._from_row(row)

    @classmethod
    async def get_all_with_custom_mxid(cls) -> List['Puppet']:
        q = ("SELECT fbid, name, photo_id, photo_mxc, name_set, avatar_set, is_registered, "
             "       custom_mxid, access_token, next_batch, base_url "
             "FROM puppet WHERE custom_mxid<>''")
        rows = await cls.db.fetch(q)
        return [cls._from_row(row) for row in rows]

    async def insert(self) -> None:
        q = ("INSERT INTO puppet (fbid, name, photo_id, photo_mxc, name_set, avatar_set, "
             "                    is_registered, custom_mxid, access_token, next_batch, base_url) "
             "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)")
        await self.db.execute(q, self.fbid, self.name, self.photo_id, self.photo_mxc,
                              self.name_set, self.avatar_set, self.is_registered, self.custom_mxid,
                              self.access_token, self.next_batch,
                              str(self.base_url) if self.base_url else None)

    async def delete(self) -> None:
        q = "DELETE FROM puppet WHERE fbid=$1"
        await self.db.execute(q, self.fbid)

    async def save(self) -> None:
        q = ('UPDATE puppet SET name=$2, photo_id=$3, photo_mxc=$4, name_set=$5, avatar_set=$6, '
             '                  is_registered=$7, custom_mxid=$8, access_token=$9, next_batch=$10,'
             '                  base_url=$11 '
             'WHERE fbid=$1')
        await self.db.execute(q, self.fbid, self.name, self.photo_id, self.photo_mxc,
                              self.name_set, self.avatar_set, self.is_registered, self.custom_mxid,
                              self.access_token, self.next_batch,
                              str(self.base_url) if self.base_url else None)
