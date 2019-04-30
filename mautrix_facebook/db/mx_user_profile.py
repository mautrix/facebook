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

from sqlalchemy import Column, String, Enum, and_
from sqlalchemy.engine.result import RowProxy

from mautrix.types import RoomID, UserID, ContentURI, Member, Membership

from .base import Base


class UserProfile(Base):
    __tablename__ = "mx_user_profile"

    room_id: RoomID = Column(String(255), primary_key=True)
    user_id: UserID = Column(String(255), primary_key=True)
    membership: Membership = Column(Enum(Membership), nullable=False, default=Membership.LEAVE)
    displayname: str = Column(String, nullable=True)
    avatar_url: ContentURI = Column(String(255), nullable=True)

    def member(self) -> Member:
        return Member(membership=self.membership, displayname=self.displayname,
                      avatar_url=self.avatar_url)

    @classmethod
    def scan(cls, row: RowProxy) -> 'UserProfile':
        room_id, user_id, membership, displayname, avatar_url = row
        return cls(room_id=room_id, user_id=user_id, membership=membership, displayname=displayname,
                   avatar_url=avatar_url)

    @classmethod
    def get(cls, room_id: RoomID, user_id: UserID) -> Optional['UserProfile']:
        return cls._select_one_or_none(and_(cls.c.room_id == room_id, cls.c.user_id == user_id))

    @classmethod
    def delete_all(cls, room_id: RoomID) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.delete().where(cls.c.room_id == room_id))

    def update(self) -> None:
        super().edit(membership=self.membership, displayname=self.displayname,
                     avatar_url=self.avatar_url, _update_values=False)

    @property
    def _edit_identity(self):
        return and_(self.c.room_id == self.room_id, self.c.user_id == self.user_id)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(room_id=self.room_id, user_id=self.user_id,
                                                membership=self.membership,
                                                displayname=self.displayname,
                                                avatar_url=self.avatar_url))
