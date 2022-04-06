# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan, Sumner Evans
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
from datetime import datetime
from enum import IntEnum

from asyncpg import Record
from attr import dataclass

from mautrix.types import EventID, RoomID
from mautrix.types.primitive import UserID
from mautrix.util.async_db import Database, Scheme

fake_db = Database.create("") if TYPE_CHECKING else None


class BackfillType(IntEnum):
    IMMEDIATE = 0
    DEFERRED = 1


@dataclass
class Backfill:
    db: ClassVar[Database] = fake_db

    queue_id: int
    user_mxid: UserID
    type: BackfillType
    priority: int
    portal_fbid: int
    portal_fb_receiver: int
    time_start: datetime | None
    time_end: datetime | None
    max_batch_events: int | None
    max_total_events: int | None
    batch_delay: int
    completed_at: datetime | None

    @classmethod
    def _from_row(cls, row: Record | None) -> Backfill | None:
        if row is None:
            return None
        return cls(**row)

    columns = ",".join(
        [
            "user_mxid",
            "type",
            "priority",
            "portal_fbid",
            "portal_fb_receiver",
            "time_start",
            "time_end",
            "max_batch_events",
            "max_total_events",
            "batch_delay",
            "completed_at",
        ]
    )

    @classmethod
    async def get_next(cls, user_mxid: UserID, backfill_type: BackfillType) -> list[Backfill]:
        q = f"""
        SELECT queue_id, {cls.columns}
          FROM backfill_queue
         WHERE user_mxid=$1
           AND type=$2
           AND completed_at IS NULL
      ORDER BY priority, queue_id
         LIMIT 1
        """
        rows = await cls.db.fetch(q, user_mxid, backfill_type)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def delete_all(cls, user_mxid: UserID) -> None:
        await cls.db.execute("DELETE FROM backfill_queue WHERE user_mxid=$1", user_mxid)

    async def insert(self) -> None:
        q = f"""
        INSERT INTO backfill_queue ({self.columns})
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING queue_id
        """
        self.queue_id = (
            await self.db.fetchrow(
                q,
                self.user_mxid,
                self.type,
                self.priority,
                self.portal_fbid,
                self.portal_fb_receiver,
                self.time_start,
                self.time_end,
                self.max_batch_events,
                self.max_total_events,
                self.batch_delay,
                self.completed_at,
            )
        )[0]

    async def mark_done(self) -> None:
        q = "UPDATE backfill_queue SET completed_at=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now(), self.queue_id)
