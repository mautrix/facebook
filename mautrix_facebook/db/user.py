# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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

from sqlalchemy import Column, Text, PickleType

from mautrix.types import UserID, RoomID
from mautrix.util.db import Base


class User(Base):
    __tablename__ = "user"

    mxid: UserID = Column(Text, primary_key=True)
    session: Dict[str, str] = Column(PickleType, nullable=True)
    fbid: str = Column(Text, nullable=True)
    notice_room: RoomID = Column(Text, nullable=True)
    user_agent: str = Column(Text, nullable=True)
    fb_domain: str = Column(Text, nullable=False, server_default="messenger.com")

    @classmethod
    def all(cls) -> Iterable['User']:
        return cls._select_all()

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['User']:
        return cls._select_one_or_none(cls.c.fbid == fbid)

    @classmethod
    def get_by_mxid(cls, mxid: UserID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.mxid == mxid)
