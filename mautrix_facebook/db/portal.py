# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from enum import Enum

from asyncpg import Record
from attr import dataclass

from maufbapi.types.graphql import ThreadKey as GraphQLThreadKey
from maufbapi.types.mqtt import ThreadKey as MQTTThreadKey
from mautrix.types import ContentURI, RoomID, UserID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


class ThreadType(Enum):
    USER = "USER"
    GROUP = "GROUP"
    PAGE = "PAGE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_thread_key(cls, thread_key: MQTTThreadKey | GraphQLThreadKey) -> ThreadType:
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
    mxid: RoomID | None
    name: str | None
    photo_id: str | None
    avatar_url: ContentURI | None
    encrypted: bool
    name_set: bool
    avatar_set: bool
    relay_user_id: UserID | None

    @classmethod
    def _from_row(cls, row: Record | None) -> Portal | None:
        if row is None:
            return None
        data = {**row}
        fb_type = ThreadType(data.pop("fb_type"))
        return cls(**data, fb_type=fb_type)

    @classmethod
    async def get_by_fbid(cls, fbid: int, fb_receiver: int) -> Portal | None:
        q = """
            SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted,
                   name_set, avatar_set, relay_user_id
            FROM portal WHERE fbid=$1 AND fb_receiver=$2
        """
        row = await cls.db.fetchrow(q, fbid, fb_receiver)
        return cls._from_row(row)

    @classmethod
    async def get_by_mxid(cls, mxid: RoomID) -> Portal | None:
        q = """
            SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted,
                   name_set, avatar_set, relay_user_id
            FROM portal WHERE mxid=$1
        """
        row = await cls.db.fetchrow(q, mxid)
        return cls._from_row(row)

    @classmethod
    async def get_all_by_receiver(cls, fb_receiver: int) -> list[Portal]:
        q = """
            SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted,
                   name_set, avatar_set, relay_user_id
            FROM portal WHERE fb_receiver=$1 AND fb_type='USER'
        """
        rows = await cls.db.fetch(q, fb_receiver)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def all(cls) -> list[Portal]:
        q = """
            SELECT fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted,
                   name_set, avatar_set, relay_user_id
            FROM portal
        """
        rows = await cls.db.fetch(q)
        return [cls._from_row(row) for row in rows]

    @property
    def _values(self):
        return (
            self.fbid,
            self.fb_receiver,
            self.fb_type.name,
            self.mxid,
            self.name,
            self.photo_id,
            self.avatar_url,
            self.encrypted,
            self.name_set,
            self.avatar_set,
            self.relay_user_id,
        )

    async def insert(self) -> None:
        q = """
        INSERT INTO portal (fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url,
                            encrypted, name_set, avatar_set, relay_user_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """
        await self.db.execute(q, *self._values)

    async def delete(self) -> None:
        q = "DELETE FROM portal WHERE fbid=$1 AND fb_receiver=$2"
        await self.db.execute(q, self.fbid, self.fb_receiver)

    async def save(self) -> None:
        q = """
            UPDATE portal SET fb_type=$3, mxid=$4, name=$5, photo_id=$6, avatar_url=$7,
                              encrypted=$8, name_set=$9, avatar_set=$10, relay_user_id=$11
            WHERE fbid=$1 AND fb_receiver=$2
        """
        await self.db.execute(q, *self._values)
