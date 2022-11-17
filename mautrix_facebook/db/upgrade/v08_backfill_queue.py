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
from mautrix.util.async_db import Connection, Scheme

from . import upgrade_table


@upgrade_table.register(description="Add the backfill queue table")
async def upgrade_v8(conn: Connection, scheme: Scheme) -> None:
    gen = ""
    if scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
        gen = "GENERATED ALWAYS AS IDENTITY"
    await conn.execute(
        f"""
        CREATE TABLE backfill_queue (
            queue_id            INTEGER PRIMARY KEY {gen},
            user_mxid           TEXT,
            priority            INTEGER NOT NULL,
            portal_fbid         BIGINT,
            portal_fb_receiver  BIGINT,
            num_pages           INTEGER NOT NULL,
            page_delay          INTEGER NOT NULL,
            post_batch_delay    INTEGER NOT NULL,
            max_total_pages     INTEGER NOT NULL,
            dispatch_time       TIMESTAMP,
            completed_at        TIMESTAMP,
            cooldown_timeout    TIMESTAMP,

            FOREIGN KEY (user_mxid) REFERENCES "user"(mxid) ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (portal_fbid, portal_fb_receiver)
                REFERENCES portal(fbid, fb_receiver) ON DELETE CASCADE
        )
        """
    )
