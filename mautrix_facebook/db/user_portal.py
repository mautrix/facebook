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
from typing import Dict, Optional

from sqlalchemy import Column, String, Boolean, ForeignKeyConstraint, and_
from sqlalchemy.sql import expression

from mautrix.util.db import Base


class UserPortal(Base):
    __tablename__ = "user_portal"

    user: str = Column(String(255), primary_key=True)
    portal: str = Column(String(255), primary_key=True)
    portal_receiver: str = Column(String(255), primary_key=True)
    in_community: bool = Column(Boolean, nullable=False, server_default=expression.false())

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.fbid", "portal.fb_receiver"),
                                           onupdate="CASCADE", ondelete="CASCADE"),)

    @classmethod
    def all(cls, user: str) -> Dict[str, 'UserPortal']:
        return {up.portal: up for up in cls._select_all(cls.c.user == user)}

    @classmethod
    def get(cls, user: str, portal: str, portal_receiver: str) -> Optional['UserPortal']:
        return cls._select_one_or_none(and_(cls.c.user == user, cls.c.portal == portal,
                                            cls.c.portal_receiver == portal_receiver))
