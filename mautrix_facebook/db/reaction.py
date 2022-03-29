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

from asyncpg import Record
from attr import dataclass

from mautrix.types import EventID, RoomID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class Reaction:
    db: ClassVar[Database] = fake_db

    mxid: EventID
    mx_room: RoomID
    fb_msgid: str
    fb_receiver: int
    fb_sender: int
    reaction: str

    @classmethod
    def _from_row(cls, row: Record | None) -> Reaction | None:
        if row is None:
            return None
        return cls(**row)

    @classmethod
    async def get_by_message_fbid(cls, fb_msgid: str, fb_receiver: int) -> dict[int, Reaction]:
        q = (
            "SELECT mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction "
            "FROM reaction WHERE fb_msgid=$1 AND fb_receiver=$2"
        )
        rows = await cls.db.fetch(q, fb_msgid, fb_receiver)
        row_gen = (cls._from_row(row) for row in rows)
        return {react.fb_sender: react for react in row_gen}

    @classmethod
    async def get_by_fbid(cls, fb_msgid: str, fb_receiver: int, fb_sender: int) -> Reaction | None:
        q = (
            "SELECT mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction "
            "FROM reaction WHERE fb_msgid=$1 AND fb_receiver=$2 AND fb_sender=$3"
        )
        row = await cls.db.fetchrow(q, fb_msgid, fb_receiver, fb_sender)
        return cls._from_row(row)

    @classmethod
    async def get_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> Reaction | None:
        q = (
            "SELECT mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction "
            "FROM reaction WHERE mxid=$1 AND mx_room=$2"
        )
        row = await cls.db.fetchrow(q, mxid, mx_room)
        return cls._from_row(row)

    @property
    def _values(self):
        return (
            self.mxid,
            self.mx_room,
            self.fb_msgid,
            self.fb_receiver,
            self.fb_sender,
            self.reaction,
        )

    async def insert(self) -> None:
        q = (
            "INSERT INTO reaction (mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction) "
            "VALUES ($1, $2, $3, $4, $5, $6)"
        )
        await self.db.execute(q, *self._values)

    async def delete(self) -> None:
        q = "DELETE FROM reaction WHERE fb_msgid=$1 AND fb_receiver=$2 AND fb_sender=$3"
        await self.db.execute(q, self.fb_msgid, self.fb_receiver, self.fb_sender)

    async def save(self) -> None:
        q = (
            "UPDATE reaction SET mxid=$1, mx_room=$2, reaction=$6 "
            "WHERE fb_msgid=$3 AND fb_receiver=$4 AND fb_sender=$5"
        )
        await self.db.execute(q, *self._values)
