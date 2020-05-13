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
from typing import Optional, Iterator

from sqlalchemy import Column, String, Enum, Boolean, false, and_

from mautrix.types import RoomID
from mautrix.util.db import Base

from enum import Enum as EnumType
from fbchat import ThreadABC, User, Group, Page


class ThreadType(EnumType):
    USER = 1
    GROUP = 2
    PAGE = 3
    UNKNOWN = 4

    @classmethod
    def from_thread(cls, thread: ThreadABC) -> 'ThreadType':
        if isinstance(thread, User):
            return cls.USER
        elif isinstance(thread, Group):
            return cls.GROUP
        elif isinstance(thread, Page):
            return cls.PAGE
        else:
            return cls.UNKNOWN


class Portal(Base):
    __tablename__ = "portal"

    # Facebook chat information
    fbid: str = Column(String(127), primary_key=True)
    fb_receiver: str = Column(String(127), primary_key=True)
    fb_type: ThreadType = Column(Enum(ThreadType), nullable=False)

    # Matrix portal information
    mxid: RoomID = Column(String(255), unique=True, nullable=True)
    encrypted: bool = Column(Boolean, nullable=False, server_default=false())

    # Facebook chat metadata
    name = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)

    @classmethod
    def get_by_fbid(cls, fbid: str, fb_receiver: str) -> Optional['Portal']:
        return cls._select_one_or_none(and_(cls.c.fbid == fbid, cls.c.fb_receiver == fb_receiver))

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @classmethod
    def get_all_by_receiver(cls, fb_receiver: str) -> Iterator['Portal']:
        return cls._select_all(and_(cls.c.fb_receiver == fb_receiver,
                                    cls.c.fb_type == ThreadType.USER))
