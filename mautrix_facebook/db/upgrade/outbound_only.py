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
from asyncpg import Connection
from . import upgrade_table

ref_mxid_exist_query = ("SELECT EXISTS(SELECT FROM information_schema.columns "
                        "WHERE table_name='user' AND column_name='ref_mxid')")


@upgrade_table.register(description="Revision to allow outbound-only users", transaction=False)
async def upgrade_outbound_only(conn: Connection) -> None:
    ref_mxid_exist = await conn.fetchval(ref_mxid_exist_query)
    if not ref_mxid_exist:
        await conn.execute('ALTER TABLE "user" ADD COLUMN ref_mxid TEXT')
