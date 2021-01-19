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
from typing import Dict, Optional, TYPE_CHECKING, ClassVar

from asyncpg import Record
from attr import dataclass

from mautrix.util.async_db import Database

fake_db = Database("") if TYPE_CHECKING else None


@dataclass
class UserContact:
    db: ClassVar[Database] = fake_db

    user: int
    contact: int
    in_community: bool

    @classmethod
    def _from_row(cls, row: Optional[Record]) -> Optional['UserContact']:
        if row is None:
            return None
        return cls(**row)

    @classmethod
    async def all(cls, user: int) -> Dict[int, 'UserContact']:
        q = ('SELECT "user", contact, in_community FROM user_contact '
             'WHERE "user"=$1')
        rows = await cls.db.fetch(q, user)
        return {up.contact: up for up in (cls._from_row(row) for row in rows)}

    @classmethod
    async def get(cls, user: int, contact: int) -> Optional['UserContact']:
        q = 'SELECT "user", contact, in_community FROM user_contact WHERE "user"=$1 AND contact=$2'
        row = await cls.db.fetchrow(q, user, contact)
        return cls._from_row(row)

    async def insert(self) -> None:
        q = 'INSERT INTO user_contact ("user", contact, in_community) VALUES ($1, $2, $3)'
        await self.db.execute(q, self.user, self.contact, self.in_community)

    async def upsert(self) -> None:
        q = ('INSERT INTO user_contact ("user", contact, in_community) VALUES ($1, $2, $3) '
             'ON CONFLICT ("user", contact) DO UPDATE SET in_community=$3')
        await self.db.execute(q, self.user, self.contact, self.in_community)

    async def delete(self) -> None:
        await self.db.execute('DELETE FROM user_contact WHERE "user"=$1 AND contact=$2',
                              self.user, self.contact)

    async def save(self) -> None:
        await self.db.execute('UPDATE user_contact SET in_community=$3 '
                              'WHERE "user"=$1 AND contact=$2',
                              self.user, self.contact, self.in_community)

    @classmethod
    async def delete_all(cls, user: int) -> None:
        await cls.db.execute('DELETE FROM user_contact WHERE "user"=$1', user)
