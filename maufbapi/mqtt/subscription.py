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
from typing import Dict
from enum import Enum
import pkgutil
import json

_raw_topic_map: Dict[str, int] = json.loads(pkgutil.get_data("maufbapi.mqtt", "topics.json"))
# Mapping from name to numeric ID
_topic_map: Dict[str, str] = {key: str(value) for key, value in _raw_topic_map.items()}
# Mapping from numeric ID to name
_reverse_topic_map: Dict[str, str] = {value: key for key, value in _topic_map.items()}


class RealtimeTopic(Enum):
    SYNC_CREATE_QUEUE = "/messenger_sync_create_queue"
    T_MS = "/t_ms"

    @property
    def encoded(self) -> str:
        return _topic_map[self.value]

    @staticmethod
    def decode(val: str) -> 'RealtimeTopic':
        return RealtimeTopic(_reverse_topic_map[val])
