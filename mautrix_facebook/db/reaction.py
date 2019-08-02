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

from sqlalchemy import Column, String, UniqueConstraint, and_
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql.expression import ClauseElement

from mautrix.types import RoomID, EventID
from mautrix.bridge.db.base import Base


class Reaction(Base):
    __tablename__ = "reaction"

    mxid: EventID = Column(String(255), nullable=False)
    mx_room: RoomID = Column(String(255), nullable=False)
    fb_msgid: str = Column(String(127), primary_key=True)
    fb_receiver: str = Column(String(127), primary_key=True)
    fb_sender: str = Column(String(127), primary_key=True)
    reaction: str = Column(String(1), nullable=False)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", name="_mx_react_id_room"),)

    @classmethod
    def scan(cls, row: RowProxy) -> 'Reaction':
        mxid, mx_room, fb_msgid, fb_receiver, fb_sender, reaction = row
        return cls(mxid=mxid, mx_room=mx_room, fb_msgid=fb_msgid, fb_receiver=fb_receiver,
                   fb_sender=fb_sender, reaction=reaction)

    @classmethod
    def get_by_fbid(cls, fb_msgid: str, fb_receiver: str, fb_sender: str) -> Optional['Reaction']:
        return cls._select_one_or_none(and_(cls.c.fb_msgid == fb_msgid,
                                            cls.c.fb_receiver == fb_receiver,
                                            cls.c.fb_sender == fb_sender))

    @classmethod
    def get_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> Optional['Reaction']:
        return cls._select_one_or_none(and_(cls.c.mxid == mxid, cls.c.mx_room == mx_room))

    @property
    def _edit_identity(self) -> ClauseElement:
        return and_(self.c.fb_msgid == self.fb_msgid, self.c.fb_receiver == self.fb_receiver,
                    self.c.fb_sender == self.fb_sender)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                mxid=self.mxid, mx_room=self.mx_room, fb_msgid=self.fb_msgid,
                fb_receiver=self.fb_receiver, fb_sender=self.fb_sender, reaction=self.reaction))
