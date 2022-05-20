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

from typing import TYPE_CHECKING, ClassVar, Iterable
from datetime import datetime
from enum import IntEnum
import asyncio

from asyncpg import Record
from attr import dataclass

from mautrix.types import UserID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


class BackfillType(IntEnum):
    IMMEDIATE = 0
    FORWARD = 100
    DEFERRED = 200


class BackfillQueue:
    def __init__(self, user_id: UserID):
        self._user_id = user_id
        self._re_check_queues: list[asyncio.Queue[bool]] = []

    def re_check(self):
        for queue in self._re_check_queues:
            try:
                queue.put_nowait(True)
            except asyncio.QueueFull:
                # This is fine, it just means that there's already a re-check request in the queue.
                pass

    def add_re_check_queue(self, queue: asyncio.Queue):
        self._re_check_queues.append(queue)

    async def get_next(self, backfill_types: Iterable[BackfillType]) -> Backfill | None:
        return await Backfill.get_next(self._user_id, backfill_types)


@dataclass
class Backfill:
    db: ClassVar[Database] = fake_db

    queue_id: int | None
    user_mxid: UserID
    type: BackfillType
    priority: int
    portal_fbid: int
    portal_fb_receiver: int
    time_start: datetime | None
    max_batch_events: int | None
    max_total_events: int | None
    batch_delay: int
    dispatch_time: datetime | None
    completed_at: datetime | None

    @staticmethod
    def new(
        user_mxid: UserID,
        backfill_type: BackfillType,
        priority: int,
        portal_fbid: int,
        portal_fb_receiver: int,
        time_start: datetime | None = None,
        max_batch_events: int | None = None,
        max_total_events: int = -1,
        batch_delay: int = 0,
    ) -> "Backfill":
        return Backfill(
            queue_id=None,
            user_mxid=user_mxid,
            type=backfill_type,
            priority=priority,
            portal_fbid=portal_fbid,
            portal_fb_receiver=portal_fb_receiver,
            time_start=time_start,
            max_batch_events=max_batch_events,
            max_total_events=max_total_events,
            batch_delay=batch_delay,
            dispatch_time=None,
            completed_at=None,
        )

    @classmethod
    def _from_row(cls, row: Record | None) -> Backfill | None:
        if row is None:
            return None
        return cls(**row)

    columns = [
        "user_mxid",
        "type",
        "priority",
        "portal_fbid",
        "portal_fb_receiver",
        "time_start",
        "max_batch_events",
        "max_total_events",
        "batch_delay",
        "dispatch_time",
        "completed_at",
    ]
    columns_str = ",".join(columns)

    @classmethod
    async def get_next(
        cls, user_mxid: UserID, backfill_types: Iterable[BackfillType]
    ) -> Backfill | None:
        q = f"""
        SELECT queue_id, {cls.columns_str}
        FROM backfill_queue
        WHERE user_mxid=$1
            AND type IN ({','.join([str(bt.value) for bt in backfill_types])})
            AND (
                dispatch_time IS NULL
                OR (
                    dispatch_time < current_timestamp - interval '15 minutes'
                    AND completed_at IS NULL
                )
            )
        ORDER BY type, priority, queue_id
        LIMIT 1
        """
        return cls._from_row(await cls.db.fetchrow(q, user_mxid))

    @classmethod
    async def delete_all(cls, user_mxid: UserID) -> None:
        await cls.db.execute("DELETE FROM backfill_queue WHERE user_mxid=$1", user_mxid)

    async def insert(self) -> None:
        q = f"""
        INSERT INTO backfill_queue ({self.columns_str})
        VALUES ({','.join(f'${i+1}' for i in range(len(self.columns)))})
        RETURNING queue_id
        """
        row = await self.db.fetchrow(
            q,
            self.user_mxid,
            self.type,
            self.priority,
            self.portal_fbid,
            self.portal_fb_receiver,
            self.time_start,
            self.max_batch_events,
            self.max_total_events,
            self.batch_delay,
            self.dispatch_time,
            self.completed_at,
        )
        self.queue_id = row["queue_id"]

    async def mark_dispatched(self) -> None:
        q = "UPDATE backfill_queue SET dispatch_time=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now(), self.queue_id)

    async def mark_done(self) -> None:
        q = "UPDATE backfill_queue SET completed_at=$1 WHERE queue_id=$2"
        await self.db.execute(q, datetime.now(), self.queue_id)
