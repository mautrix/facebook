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

from mautrix.types import RoomID, EventID
from mautrix.util.async_db import Database

fake_db = Database("") if TYPE_CHECKING else None


@dataclass
class Message:
    db: ClassVar[Database] = fake_db

    mxid: EventID
    mx_room: RoomID
    fbid: Optional[str]
    fb_txn_id: Optional[int]
    index: int
    fb_chat: int
    fb_receiver: int
    fb_sender: int
    timestamp: int

    @classmethod
    def _from_row(cls, row: Optional[Record]) -> Optional['Message']:
        if row is None:
            return None
        return cls(**row)

    columns = "mxid, mx_room, fbid, fb_txn_id, index, fb_chat, fb_receiver, fb_sender, timestamp"

    @classmethod
    async def get_all_by_fbid(cls, fbid: str, fb_receiver: int) -> List['Message']:
        q = f"SELECT {cls.columns} FROM message WHERE fbid=$1 AND fb_receiver=$2"
        rows = await cls.db.fetch(q, fbid, fb_receiver)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def get_by_fbid(cls, fbid: str, fb_receiver: int, index: int = 0) -> Optional['Message']:
        q = f"SELECT {cls.columns} FROM message WHERE fbid=$1 AND fb_receiver=$2 AND index=$3"
        row = await cls.db.fetchrow(q, fbid, fb_receiver, index)
        return cls._from_row(row)

    @classmethod
    async def get_by_fbid_or_oti(cls, fbid: str, oti: int, fb_receiver: int, fb_sender: int,
                                 index: int = 0) -> Optional['Message']:
        q = (f"SELECT {cls.columns} "
             "FROM message WHERE (fbid=$1 OR (fb_txn_id=$2 AND fb_sender=$3)) AND "
             "                   fb_receiver=$4 AND index=$5")
        row = await cls.db.fetchrow(q, fbid, oti, fb_sender, fb_receiver, index)
        return cls._from_row(row)

    @classmethod
    async def delete_all_by_room(cls, room_id: RoomID) -> None:
        await cls.db.execute("DELETE FROM message WHERE mx_room=$1", room_id)

    @classmethod
    async def get_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> Optional['Message']:
        q = (f"SELECT {cls.columns} "
             "FROM message WHERE mxid=$1 AND mx_room=$2")
        row = await cls.db.fetchrow(q, mxid, mx_room)
        return cls._from_row(row)

    @classmethod
    async def get_most_recent(cls, fb_chat: int, fb_receiver: int) -> Optional['Message']:
        q = (f"SELECT {cls.columns} "
             "FROM message WHERE fb_chat=$1 AND fb_receiver=$2 AND fbid IS NOT NULL "
             "ORDER BY timestamp DESC LIMIT 1")
        row = await cls.db.fetchrow(q, fb_chat, fb_receiver)
        return cls._from_row(row)

    @classmethod
    async def get_closest_before(cls, fb_chat: int, fb_receiver: int, timestamp: int
                                 ) -> Optional['Message']:
        q = (f"SELECT {cls.columns} "
             "FROM message WHERE fb_chat=$1 AND fb_receiver=$2 AND timestamp<=$3 AND "
             "                   fbid IS NOT NULL "
             "ORDER BY timestamp DESC LIMIT 1")
        row = await cls.db.fetchrow(q, fb_chat, fb_receiver, timestamp)
        return cls._from_row(row)

    @classmethod
    async def bulk_create(cls, fbid: str, oti: int, fb_chat: int, fb_receiver: int, fb_sender: int,
                          event_ids: List[EventID], timestamp: int, mx_room: RoomID) -> None:
        if not event_ids:
            return
        columns = cls.columns.split(", ")
        records = [(mxid, mx_room, fbid, oti, index, fb_chat, fb_receiver, fb_sender, timestamp)
                   for index, mxid in enumerate(event_ids)]
        async with cls.db.acquire() as conn, conn.transaction():
            await conn.copy_records_to_table("message", records=records, columns=columns)

    async def insert(self) -> None:
        q = ("INSERT INTO message (mxid, mx_room, fbid, fb_txn_id, index, fb_chat, fb_receiver, "
             "                     fb_sender, timestamp) "
             "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)")
        await self.db.execute(q, self.mxid, self.mx_room, self.fbid, self.fb_txn_id, self.index,
                              self.fb_chat, self.fb_receiver, self.fb_sender, self.timestamp)

    async def delete(self) -> None:
        q = "DELETE FROM message WHERE fbid=$1 AND fb_receiver=$2 AND index=$3"
        await self.db.execute(q, self.fbid, self.fb_receiver, self.index)

    async def update(self) -> None:
        await self.db.execute("UPDATE message SET fbid=$1, timestamp=$2 "
                              "WHERE mxid=$3 AND mx_room=$4",
                              self.fbid, self.timestamp, self.mxid, self.mx_room)
