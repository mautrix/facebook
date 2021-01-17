# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2021 Tulir Asokan
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
from datetime import timezone
from enum import Enum as EnumType

from sqlalchemy import (Column, Text, SmallInteger, Boolean, Enum, PickleType, UniqueConstraint,
                        ForeignKey, ForeignKeyConstraint, false, types)

from mautrix.util.db import Base


class UTCDateTime(types.TypeDecorator):
    impl = types.DateTime

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            elif value.tzinfo != timezone.utc:
                value = value.astimezone(timezone.utc)

        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        else:
            return value


class ThreadType(EnumType):
    USER = 1
    GROUP = 2
    PAGE = 3
    UNKNOWN = 4


class Message(Base):
    __tablename__ = "message"

    mxid = Column(Text, nullable=False)
    mx_room = Column(Text, nullable=False)
    fbid = Column(Text, primary_key=True)
    fb_chat = Column(Text, nullable=True)
    fb_receiver = Column(Text, primary_key=True)
    index = Column(SmallInteger, primary_key=True, default=0)
    date = Column(UTCDateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", name="_mx_id_room"),)


class Portal(Base):
    __tablename__ = "portal"

    fbid = Column(Text, primary_key=True)
    fb_receiver = Column(Text, primary_key=True)
    fb_type = Column(Enum(ThreadType), nullable=False)

    mxid = Column(Text, unique=True, nullable=True)
    avatar_url = Column(Text, nullable=True)
    encrypted = Column(Boolean, nullable=False, server_default=false())

    name = Column(Text, nullable=True)
    photo_id = Column(Text, nullable=True)


class Puppet(Base):
    __tablename__ = "puppet"

    fbid = Column(Text, primary_key=True)
    name = Column(Text, nullable=True)
    name_set = Column(Boolean, nullable=False, server_default=false())
    photo_id = Column(Text, nullable=True)
    avatar_set = Column(Boolean, nullable=False, server_default=false())
    matrix_registered = Column(Boolean, nullable=False, server_default=false())

    custom_mxid = Column(Text, nullable=True)
    access_token = Column(Text, nullable=True)
    next_batch = Column(Text, nullable=True)
    base_url = Column(Text, nullable=True)


class Reaction(Base):
    __tablename__ = "reaction"

    mxid = Column(Text, nullable=False)
    mx_room = Column(Text, nullable=False)
    fb_msgid = Column(Text, primary_key=True)
    fb_receiver = Column(Text, primary_key=True)
    fb_sender = Column(Text, primary_key=True)
    reaction = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", name="_mx_react_id_room"),)


class User(Base):
    __tablename__ = "user"

    mxid = Column(Text, primary_key=True)
    session = Column(PickleType, nullable=True)
    fbid = Column(Text, nullable=True)
    notice_room = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    fb_domain = Column(Text, nullable=False, server_default="messenger.com")


class Contact(Base):
    __tablename__ = "contact"

    user = Column(Text, primary_key=True)
    contact = Column(Text, ForeignKey("puppet.fbid"), primary_key=True)
    in_community = Column(Boolean, nullable=False, server_default=false())


class UserPortal(Base):
    __tablename__ = "user_portal"

    user = Column(Text, primary_key=True)
    portal = Column(Text, primary_key=True)
    portal_receiver = Column(Text, primary_key=True)
    in_community = Column(Boolean, nullable=False, server_default=false())

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.fbid", "portal.fb_receiver"),
                                           onupdate="CASCADE", ondelete="CASCADE"),)
