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

from mautrix.types import UserID, RoomID
from mautrix.util.async_db import Database
from maufbapi import AndroidState

fake_db = Database("") if TYPE_CHECKING else None


@dataclass
class User:
    db: ClassVar[Database] = fake_db

    mxid: UserID
    fbid: Optional[int]
    state: Optional[AndroidState]
    notice_room: Optional[RoomID]
    ref_mxid: Optional[UserID]

    @property
    def _state_json(self) -> Optional[str]:
        return self.state.json() if self.state else None

    @classmethod
    async def _from_row(cls, row: Optional[Record]) -> Optional['User']:
        if row is None:
            return None
        data = {**row}
        state = data.pop("state", None)
        user = cls(**data, state=AndroidState.parse_json(state) if state else None)
        if user.is_outbound:
            await user.init_from_ref_user()
        return user

    @classmethod
    async def all_logged_in(cls) -> List['User']:
        rows = await cls.db.fetch('SELECT mxid, fbid, state, notice_room, ref_mxid FROM "user" '
                                  "WHERE fbid<>0 AND state IS NOT NULL AND ref_mxid IS NULL")
        return [await cls._from_row(row) for row in rows]

    @classmethod
    async def get_by_fbid(cls, fbid: int) -> Optional['User']:
        q = 'SELECT mxid, fbid, state, notice_room, ref_mxid FROM "user" WHERE fbid=$1'
        row = await cls.db.fetchrow(q, fbid)
        return await cls._from_row(row)

    @classmethod
    async def get_by_mxid(cls, mxid: UserID) -> Optional['User']:
        q = 'SELECT mxid, fbid, state, notice_room, ref_mxid FROM "user" WHERE mxid=$1'
        row = await cls.db.fetchrow(q, mxid)
        return await cls._from_row(row)

    async def insert(self) -> None:
        q = 'INSERT INTO "user" (mxid, fbid, state, notice_room, ref_mxid) VALUES ($1, $2, $3, $4, $5)'
        await self.db.execute(q, self.mxid, self.fbid, self._state_json, self.notice_room, self.ref_mxid)

    async def delete(self) -> None:
        await self.db.execute('DELETE FROM "user" WHERE mxid=$1', self.mxid)

    async def save(self) -> None:
        await self.db.execute('UPDATE "user" SET fbid=$2, state=$3, notice_room=$4, ref_mxid=$5 WHERE mxid=$1',
                              self.mxid, self.fbid, self._state_json, self.notice_room, self.ref_mxid)
