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
from mautrix.util.async_db import Connection

from . import upgrade_table


@upgrade_table.register(description="Add offline threading ID to message table")
async def upgrade_v2(conn: Connection) -> None:
    await conn.execute("ALTER TABLE message RENAME TO message_v1")
    await conn.execute("DELETE FROM message_v1 WHERE fb_chat IS NULL")
    await conn.execute(
        """CREATE TABLE message (
            mxid        TEXT NOT NULL,
            mx_room     TEXT NOT NULL,
            fbid        TEXT,
            fb_txn_id   BIGINT,
            "index"     SMALLINT NOT NULL,
            fb_chat     BIGINT NOT NULL,
            fb_receiver BIGINT NOT NULL,
            fb_sender   BIGINT NOT NULL,
            timestamp   BIGINT NOT NULL,
            FOREIGN KEY (fb_chat, fb_receiver) REFERENCES portal(fbid, fb_receiver)
                ON UPDATE CASCADE ON DELETE CASCADE,
            UNIQUE (mxid, mx_room),
            UNIQUE (fbid, fb_receiver, "index"),
            UNIQUE (fb_txn_id, fb_sender, fb_receiver, "index")
        )"""
    )
    await conn.execute(
        'INSERT INTO message (mxid, mx_room, fbid, "index", fb_chat, fb_receiver, fb_sender, '
        "                     timestamp) "
        'SELECT mxid, mx_room, fbid, COALESCE("index", 0), fb_chat, fb_receiver, 0, '
        "       COALESCE(timestamp, 0) "
        "FROM message_v1"
    )
    await conn.execute("DROP TABLE message_v1")
