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
from mautrix.util.async_db import Connection, Scheme

from . import upgrade_table


@upgrade_table.register(description="Remove community-related fields")
async def upgrade_v5(conn: Connection, scheme: Scheme) -> None:
    await conn.execute("DROP TABLE user_contact")
    if scheme != Scheme.SQLITE:
        await conn.execute("ALTER TABLE user_portal DROP COLUMN in_community")
    else:
        await conn.execute(
            """CREATE TABLE user_portal_v5 (
                "user"          BIGINT,
                portal          BIGINT,
                portal_receiver BIGINT,
                FOREIGN KEY (portal, portal_receiver) REFERENCES portal(fbid, fb_receiver)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                FOREIGN KEY ("user") REFERENCES "user"(fbid) ON UPDATE CASCADE ON DELETE CASCADE,
                PRIMARY KEY ("user", portal, portal_receiver)
            )"""
        )
        await conn.execute(
            """
            INSERT INTO user_portal_v5 ("user", portal, portal_receiver)
            SELECT "user", portal, portal_receiver FROM user_portal
            """
        )
        await conn.execute("DROP TABLE user_portal")
        await conn.execute("ALTER TABLE user_portal_v5 RENAME TO user_portal")
