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
from typing import Optional, Iterable, Dict

from sqlalchemy import Column, String, PickleType

from mautrix.types import UserID, RoomID
from mautrix.util.db import Base


class User(Base):
    __tablename__ = "user"

    mxid: UserID = Column(String(255), primary_key=True)
    session: Dict[str, str] = Column(PickleType, nullable=True)
    fbid: str = Column(String(255), nullable=True)
    notice_room: RoomID = Column(String(255), nullable=True)

    @classmethod
    def all(cls) -> Iterable['User']:
        return cls._select_all()

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['User']:
        return cls._select_one_or_none(cls.c.fbid == fbid)

    @classmethod
    def get_by_mxid(cls, mxid: UserID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.mxid == mxid)
