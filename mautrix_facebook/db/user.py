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

from maufbapi import AndroidState
from mautrix.types import RoomID, UserID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class User:
    db: ClassVar[Database] = fake_db

    mxid: UserID
    fbid: int | None
    state: AndroidState | None
    notice_room: RoomID | None

    @property
    def _state_json(self) -> str | None:
        return self.state.json() if self.state else None

    @classmethod
    def _from_row(cls, row: Record | None) -> User | None:
        if row is None:
            return None
        data = {**row}
        state = data.pop("state", None)
        return cls(**data, state=AndroidState.parse_json(state) if state else None)

    @classmethod
    async def all_logged_in(cls) -> list[User]:
        q = """
            SELECT mxid, fbid, state, notice_room FROM "user"
            WHERE fbid<>0
        """
        rows = await cls.db.fetch(q)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def get_by_fbid(cls, fbid: int) -> User | None:
        q = 'SELECT mxid, fbid, state, notice_room FROM "user" WHERE fbid=$1'
        row = await cls.db.fetchrow(q, fbid)
        return cls._from_row(row)

    @classmethod
    async def get_by_mxid(cls, mxid: UserID) -> User | None:
        q = 'SELECT mxid, fbid, state, notice_room FROM "user" WHERE mxid=$1'
        row = await cls.db.fetchrow(q, mxid)
        return cls._from_row(row)

    async def insert(self) -> None:
        q = 'INSERT INTO "user" (mxid, fbid, state, notice_room) VALUES ($1, $2, $3, $4)'
        await self.db.execute(q, self.mxid, self.fbid, self._state_json, self.notice_room)

    async def delete(self) -> None:
        await self.db.execute('DELETE FROM "user" WHERE mxid=$1', self.mxid)

    async def save(self) -> None:
        q = 'UPDATE "user" SET fbid=$1, state=$2, notice_room=$3 WHERE mxid=$4'
        await self.db.execute(q, self.fbid, self._state_json, self.notice_room, self.mxid)
