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
from typing import Optional

from sqlalchemy import Column, Text, UniqueConstraint, and_

from mautrix.types import RoomID, EventID
from mautrix.util.db import Base


class Reaction(Base):
    __tablename__ = "reaction"

    mxid: EventID = Column(Text, nullable=False)
    mx_room: RoomID = Column(Text, nullable=False)
    fb_msgid: str = Column(Text, primary_key=True)
    fb_receiver: str = Column(Text, primary_key=True)
    fb_sender: str = Column(Text, primary_key=True)
    reaction: str = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", name="_mx_react_id_room"),)

    @classmethod
    def get_by_fbid(cls, fb_msgid: str, fb_receiver: str, fb_sender: str) -> Optional['Reaction']:
        return cls._select_one_or_none(and_(cls.c.fb_msgid == fb_msgid,
                                            cls.c.fb_receiver == fb_receiver,
                                            cls.c.fb_sender == fb_sender))

    @classmethod
    def get_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> Optional['Reaction']:
        return cls._select_one_or_none(and_(cls.c.mxid == mxid, cls.c.mx_room == mx_room))
