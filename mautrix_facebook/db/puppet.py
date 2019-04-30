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

from sqlalchemy import Column, String, Boolean
from sqlalchemy.sql import expression
from sqlalchemy.engine.result import RowProxy

from .base import Base


class Puppet(Base):
    __tablename__ = "puppet"

    fbid: str = Column(String, primary_key=True)
    name: str = Column(String, nullable=True)
    photo_id: str = Column(String, nullable=True)
    matrix_registered: bool = Column(Boolean, nullable=False, server_default=expression.false())

    @classmethod
    def scan(cls, row: RowProxy) -> Optional['Puppet']:
        fbid, name, photo_id, matrix_registered = row
        return cls(fbid=fbid, name=name, photo_id=photo_id, matrix_registered=matrix_registered)

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.fbid == fbid)

    @classmethod
    def get_by_name(cls, name: str) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.name == name)

    @property
    def _edit_identity(self):
        return self.c.fbid == self.fbid

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                fbid=self.fbid, name=self.name, photo_id=self.photo_id,
                matrix_registered=self.matrix_registered))
