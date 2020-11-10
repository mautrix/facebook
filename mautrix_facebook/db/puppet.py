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

from sqlalchemy import Column, Text, Boolean
from sqlalchemy.sql import expression

from mautrix.types import UserID, SyncToken
from mautrix.util.db import Base


class Puppet(Base):
    __tablename__ = "puppet"

    fbid: str = Column(Text, primary_key=True)
    name: str = Column(Text, nullable=True)
    name_set: bool = Column(Boolean, nullable=False, server_default=expression.false())
    photo_id: str = Column(Text, nullable=True)
    avatar_set: bool = Column(Boolean, nullable=False, server_default=expression.false())
    matrix_registered: bool = Column(Boolean, nullable=False, server_default=expression.false())

    custom_mxid: UserID = Column(Text, nullable=True)
    access_token: str = Column(Text, nullable=True)
    next_batch: SyncToken = Column(Text, nullable=True)
    base_url: str = Column(Text, nullable=True)

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.fbid == fbid)

    @classmethod
    def get_by_name(cls, name: str) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.name == name)

    @classmethod
    def get_by_custom_mxid(cls, mxid: UserID) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.custom_mxid == mxid)

    @classmethod
    def get_all_with_custom_mxid(cls) -> Iterator['Puppet']:
        return cls._select_all(cls.c.custom_mxid != None)
