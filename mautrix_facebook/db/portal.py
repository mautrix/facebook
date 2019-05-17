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
from typing import Optional, Iterator

from sqlalchemy import Column, String, Enum, and_
from sqlalchemy.engine.result import RowProxy

from fbchat.models import ThreadType
from mautrix.types import RoomID
from mautrix.bridge.db.base import Base


class Portal(Base):
    __tablename__ = "portal"

    # Facebook chat information
    fbid: str = Column(String(127), primary_key=True)
    fb_receiver: str = Column(String(127), primary_key=True)
    fb_type: ThreadType = Column(Enum(ThreadType), nullable=False)

    # Matrix portal information
    mxid: RoomID = Column(String(255), unique=True, nullable=True)

    # Facebook chat metadata
    name = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)

    @classmethod
    def scan(cls, row: RowProxy) -> Optional['Portal']:
        fbid, fb_receiver, fb_type, mxid, name, photo_id = row
        return cls(fbid=fbid, fb_receiver=fb_receiver, fb_type=fb_type, mxid=mxid,
                   name=name, photo_id=photo_id)

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

    @property
    def _edit_identity(self):
        return and_(self.c.fbid == self.fbid, self.c.fb_receiver == self.fb_receiver)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(fbid=self.fbid, fb_receiver=self.fb_receiver,
                                                fb_type=self.fb_type, mxid=self.mxid,
                                                name=self.name, photo_id=self.photo_id))
