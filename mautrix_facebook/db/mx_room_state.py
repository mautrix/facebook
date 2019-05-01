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
import json

from sqlalchemy import Column, String, types
from sqlalchemy.engine.result import RowProxy

from mautrix.types import RoomID, PowerLevelStateEventContent

from .base import Base


class PowerLevelType(types.TypeDecorator):
    impl = types.Text

    @property
    def python_type(self):
        return PowerLevelStateEventContent

    def process_bind_param(self, value: PowerLevelStateEventContent, dialect) -> Optional[Dict]:
        if value is not None:
            return json.dumps(value.serialize())
        return None

    def process_result_value(self, value: Dict, dialect) -> Optional[PowerLevelStateEventContent]:
        if value is not None:
            return PowerLevelStateEventContent.deserialize(json.loads(value))
        return None

    def process_literal_param(self, value, dialect):
        return value


class RoomState(Base):
    __tablename__ = "mx_room_state"

    room_id: RoomID = Column(String(255), primary_key=True)
    power_levels: PowerLevelStateEventContent = Column("power_levels", PowerLevelType,
                                                       nullable=True)

    @property
    def has_power_levels(self) -> bool:
        return bool(self.power_levels)

    @classmethod
    def scan(cls, row: RowProxy) -> 'RoomState':
        room_id, power_levels = row
        return cls(room_id=room_id, power_levels=power_levels)

    @classmethod
    def get(cls, room_id: RoomID) -> Optional['RoomState']:
        return cls._select_one_or_none(cls.c.room_id == room_id)

    def update(self) -> None:
        self.edit(power_levels=self.power_levels, _update_values=False)

    @property
    def _edit_identity(self):
        return self.c.room_id == self.room_id

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(room_id=self.room_id,
                                                power_levels=self.power_levels))
