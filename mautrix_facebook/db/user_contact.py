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
from typing import Dict

from sqlalchemy import Column, String, Boolean, ForeignKey
from sqlalchemy.sql import expression

from mautrix.util.db import Base


class Contact(Base):
    __tablename__ = "contact"

    user: str = Column(String(255), primary_key=True)
    contact: str = Column(String(255), ForeignKey("puppet.fbid"), primary_key=True)
    in_community: bool = Column(Boolean, nullable=False, server_default=expression.false())

    @classmethod
    def all(cls, user: str) -> Dict[str, 'Contact']:
        return {c.contact: c for c in cls._select_all(cls.c.user == user)}
