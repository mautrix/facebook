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
from mautrix.util.async_db import Connection

from . import upgrade_table


@upgrade_table.register(description="Add the backfill queue table")
async def upgrade_v7(conn: Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE backfill_queue (
            queue_id            INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            user_mxid           TEXT,
            type                INTEGER NOT NULL,
            priority            INTEGER NOT NULL,
            portal_fbid         BIGINT,
            portal_fb_receiver  BIGINT,
            time_start          TIMESTAMP,
            time_end            TIMESTAMP,
            max_batch_events    INTEGER NOT NULL,
            max_total_events    INTEGER,
            batch_delay         INTEGER,
            completed_at        TIMESTAMP,

            FOREIGN KEY (user_mxid) REFERENCES "user"(mxid) ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (portal_fbid, portal_fb_receiver)
                REFERENCES portal(fbid, fb_receiver) ON DELETE CASCADE
        )
        """
    )
