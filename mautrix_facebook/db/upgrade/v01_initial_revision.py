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
from asyncpg import (Connection, UndefinedObjectError, DuplicateObjectError,
                     ForeignKeyViolationError)
from . import upgrade_table

legacy_exist_query = ("SELECT EXISTS(SELECT FROM information_schema.tables "
                      "              WHERE table_name='alembic_version')")
legacy_version_query = "SELECT version_num FROM alembic_version"
last_legacy_version = "f91274813e8c"
legacy_renamed_query = ("SELECT EXISTS(SELECT FROM information_schema.tables "
                        "              WHERE table_name='legacy_contact')")
new_tables_created_query = ("SELECT EXISTS(SELECT FROM information_schema.tables "
                            "              WHERE table_name='user_contact')")


@upgrade_table.register(description="Initial asyncpg revision", transaction=False)
async def upgrade_v1(conn: Connection) -> None:
    try:
        async with conn.transaction():
            await conn.execute("CREATE TYPE threadtype AS ENUM "
                               "('USER', 'GROUP', 'PAGE', 'UNKNOWN')")
    except DuplicateObjectError:
        pass

    is_legacy = await conn.fetchval(legacy_exist_query)
    if is_legacy:
        legacy_version = await conn.fetchval(legacy_version_query)
        if legacy_version != last_legacy_version:
            raise RuntimeError("Legacy database is not on last version. Please upgrade the old "
                               "database with alembic or drop it completely first.")
        already_renamed = await conn.fetchval(legacy_renamed_query)
        if not already_renamed:
            async with conn.transaction():
                await rename_legacy_tables(conn)
        new_created = await conn.fetchval(new_tables_created_query)
        if not new_created:
            async with conn.transaction():
                await create_v1_tables(conn)
        async with conn.transaction():
            await migrate_legacy_data(conn)
    else:
        await create_v1_tables(conn)


async def create_v1_tables(conn: Connection) -> None:
    await conn.execute("""CREATE TABLE "user" (
        mxid        TEXT PRIMARY KEY,
        fbid        BIGINT UNIQUE,
        state       jsonb,
        notice_room TEXT
    )""")
    await conn.execute("""CREATE TABLE portal (
        fbid        BIGINT,
        fb_receiver BIGINT,
        fb_type     threadtype NOT NULL,
        mxid        TEXT UNIQUE,
        name        TEXT,
        photo_id    TEXT,
        avatar_url  TEXT,
        encrypted   BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (fbid, fb_receiver)
    )""")
    await conn.execute("""CREATE TABLE puppet (
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
    )""")
    await conn.execute("""CREATE TABLE message (
        mxid        TEXT,
        mx_room     TEXT,
        fbid        TEXT,
        fb_receiver BIGINT,
        index       SMALLINT,
        fb_chat     BIGINT,
        timestamp   BIGINT,
        PRIMARY KEY (fbid, fb_receiver, index),
        FOREIGN KEY (fb_chat, fb_receiver) REFERENCES portal(fbid, fb_receiver)
            ON UPDATE CASCADE ON DELETE CASCADE,
        UNIQUE (mxid, mx_room)
    )""")
    await conn.execute("""CREATE TABLE reaction (
        mxid        TEXT,
        mx_room     TEXT,
        fb_msgid    TEXT,
        fb_receiver BIGINT,
        fb_sender   BIGINT,
        reaction    TEXT,
        PRIMARY KEY (fb_msgid, fb_receiver, fb_sender),
        UNIQUE (mxid, mx_room)
    )""")
    await conn.execute("""CREATE TABLE user_portal (
        "user"          BIGINT,
        portal          BIGINT,
        portal_receiver BIGINT,
        in_community    BOOLEAN DEFAULT false,
        FOREIGN KEY (portal, portal_receiver) REFERENCES portal(fbid, fb_receiver)
            ON UPDATE CASCADE ON DELETE CASCADE,
        FOREIGN KEY ("user") REFERENCES "user"(fbid) ON UPDATE CASCADE ON DELETE CASCADE,
        PRIMARY KEY ("user", portal, portal_receiver)
    )""")
    await conn.execute("""CREATE TABLE user_contact (
        "user"       BIGINT,
        contact      BIGINT,
        in_community BOOLEAN DEFAULT false,
        FOREIGN KEY (contact) REFERENCES puppet(fbid)  ON UPDATE CASCADE ON DELETE CASCADE,
        FOREIGN KEY ("user") REFERENCES "user"(fbid) ON UPDATE CASCADE ON DELETE CASCADE,
        PRIMARY KEY ("user", contact)
    )""")


async def rename_legacy_tables(conn: Connection) -> None:
    await conn.execute("ALTER TABLE mx_user_profile RENAME TO legacy_mx_user_profile")
    await conn.execute("ALTER TABLE mx_room_state RENAME TO legacy_mx_room_state")
    try:
        async with conn.transaction():
            await conn.execute("ALTER TYPE membership RENAME TO legacy_membership")
    except UndefinedObjectError:
        pass

    await conn.execute("ALTER TABLE message RENAME TO legacy_message")
    await conn.execute("ALTER TABLE portal RENAME TO legacy_portal")
    await conn.execute("ALTER TABLE puppet RENAME TO legacy_puppet")
    await conn.execute("ALTER TABLE reaction RENAME TO legacy_reaction")
    await conn.execute('ALTER TABLE "user" RENAME TO legacy_user')
    await conn.execute("ALTER TABLE user_portal RENAME TO legacy_user_portal")
    await conn.execute("ALTER TABLE contact RENAME TO legacy_contact")


async def migrate_legacy_data(conn: Connection) -> None:
    await conn.execute('INSERT INTO "user" (mxid, fbid, notice_room) '
                       "SELECT mxid, fbid::bigint, notice_room FROM legacy_user")
    await conn.execute(
        "INSERT INTO portal (fbid, fb_receiver, fb_type, mxid, name, photo_id, encrypted) "
        "SELECT fbid::bigint, fb_receiver::bigint, fb_type::threadtype, mxid, name, photo_id, "
        "       encrypted "
        "FROM legacy_portal"
    )
    await conn.execute(
        "INSERT INTO puppet (fbid, name, photo_id, name_set, avatar_set, is_registered, "
        "                    custom_mxid, access_token, next_batch, base_url) "
        "SELECT fbid::bigint, name, photo_id, name_set, avatar_set, matrix_registered, "
        "       custom_mxid, access_token, next_batch, base_url "
        "FROM legacy_puppet WHERE fbid ~ '^[0-9]+$'"
    )
    await conn.execute(
        "INSERT INTO message (mxid, mx_room, fbid, fb_receiver, index, fb_chat, timestamp) "
        "SELECT mxid, mx_room, fbid, fb_receiver::bigint, index, fb_chat::bigint, "
        "       (extract(epoch from date) * 1000)::bigint "
        "FROM legacy_message"
    )
    await conn.execute(
        "INSERT INTO reaction (mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction) "
        "SELECT mxid, mx_room, fb_msgid, fb_receiver::bigint, fb_sender::bigint, reaction "
        "FROM legacy_reaction"
    )
    try:
        async with conn.transaction():
            await conn.execute(
                'INSERT INTO user_portal ("user", portal, portal_receiver, in_community) '
                'SELECT "user"::bigint, portal::bigint, portal_receiver::bigint, in_community '
                'FROM legacy_user_portal')
            await conn.execute(
                'INSERT INTO user_contact ("user", contact, in_community) '
                'SELECT "user"::bigint, contact::bigint, in_community '
                "FROM legacy_contact")
    except ForeignKeyViolationError:
        pass
    await conn.execute("UPDATE portal SET fb_receiver=0 WHERE fb_type<>'USER'")
    await conn.execute("UPDATE reaction SET fb_receiver=0 WHERE fb_receiver "
                       "IN (SELECT fbid FROM portal WHERE fb_receiver=0)")
