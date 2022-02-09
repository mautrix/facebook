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
from mautrix.util.async_db import Connection


async def create_v6_tables(conn: Connection) -> int:
    await conn.execute(
        """CREATE TABLE "user" (
            mxid        TEXT PRIMARY KEY,
            fbid        BIGINT UNIQUE,
            state       jsonb,
            notice_room TEXT,
            seq_id      BIGINT,
            connect_token_hash bytea
        )"""
    )
    await conn.execute(
        """CREATE TABLE portal (
            fbid        BIGINT,
            fb_receiver BIGINT,
            fb_type     threadtype NOT NULL,
            mxid        TEXT UNIQUE,
            name        TEXT,
            photo_id    TEXT,
            avatar_url  TEXT,
            encrypted   BOOLEAN NOT NULL DEFAULT false,
            name_set    BOOLEAN NOT NULL DEFAULT false,
            avatar_set  BOOLEAN NOT NULL DEFAULT false,
            relay_user_id TEXT,

            PRIMARY KEY (fbid, fb_receiver)
        )"""
    )
    await conn.execute(
        """CREATE TABLE puppet (
            fbid      BIGINT PRIMARY KEY,
            name      TEXT,
            photo_id  TEXT,
            photo_mxc TEXT,

            name_set      BOOLEAN NOT NULL DEFAULT false,
            avatar_set    BOOLEAN NOT NULL DEFAULT false,
            is_registered BOOLEAN NOT NULL DEFAULT false,

            custom_mxid  TEXT,
            access_token TEXT,
            next_batch   TEXT,
            base_url     TEXT
        )"""
    )
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
        """CREATE TABLE reaction (
            mxid        TEXT,
            mx_room     TEXT,
            fb_msgid    TEXT,
            fb_receiver BIGINT,
            fb_sender   BIGINT,
            reaction    TEXT,
            PRIMARY KEY (fb_msgid, fb_receiver, fb_sender),
            UNIQUE (mxid, mx_room)
        )"""
    )
    await conn.execute(
        """CREATE TABLE user_portal (
            "user"          BIGINT,
            portal          BIGINT,
            portal_receiver BIGINT,
            FOREIGN KEY (portal, portal_receiver) REFERENCES portal(fbid, fb_receiver)
                ON UPDATE CASCADE ON DELETE CASCADE,
            FOREIGN KEY ("user") REFERENCES "user"(fbid) ON UPDATE CASCADE ON DELETE CASCADE,
            PRIMARY KEY ("user", portal, portal_receiver)
        )"""
    )
    return 6
