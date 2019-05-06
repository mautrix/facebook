# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Optional, Iterable
from http.cookies import SimpleCookie

from sqlalchemy import Column, String, PickleType
from sqlalchemy.engine.result import RowProxy

from mautrix.types import UserID
from mautrix.bridge.db.base import Base


class User(Base):
    __tablename__ = "user"

    mxid: UserID = Column(String(255), primary_key=True)
    session: SimpleCookie = Column(PickleType, nullable=True)
    fbid: str = Column(String(255), nullable=True)

    @classmethod
    def scan(cls, row: RowProxy) -> 'User':
        mxid, session, fbid = row
        return cls(mxid=mxid, session=session, fbid=fbid)

    @classmethod
    def all(cls) -> Iterable['User']:
        return cls._select_all()

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['User']:
        return cls._select_one_or_none(cls.c.fbid == fbid)

    @classmethod
    def get_by_mxid(cls, mxid: UserID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @property
    def _edit_identity(self):
        return self.c.mxid == self.mxid

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(mxid=self.mxid, session=self.session,
                                                fbid=self.fbid))
