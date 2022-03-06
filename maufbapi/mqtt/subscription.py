# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from enum import Enum
import json
import pkgutil

_raw_topic_map: dict[str, int] = json.loads(pkgutil.get_data("maufbapi.mqtt", "topics.json"))
# Mapping from name to numeric ID
topic_map: dict[str, str] = {key: str(value) for key, value in _raw_topic_map.items()}
# Mapping from numeric ID to name
_reverse_topic_map: dict[str, str] = {value: key for key, value in topic_map.items()}


class RealtimeTopic(Enum):
    SYNC_CREATE_QUEUE = "/messenger_sync_create_queue"
    SYNC_RESUME_QUEUE = "/t_ms_gd"
    MESSAGE_SYNC = "/t_ms"
    SEND_MESSAGE = "/t_sm"
    SEND_MESSAGE_RESP = "/t_sm_rp"
    REGION_HINT = "/t_region_hint"
    MARK_THREAD_READ = "/t_mt_req"
    MARK_THREAD_READ_RESPONSE = "/t_mt_resp"
    OPENED_THREAD = "/opened_thread"
    TYPING_NOTIFICATION = "/t_tn"
    SET_TYPING = "/t_st"
    ORCA_PRESENCE = "/orca_presence"

    @property
    def encoded(self) -> str:
        return topic_map[self.value]

    @staticmethod
    def decode(val: str) -> RealtimeTopic | str:
        topic = _reverse_topic_map[val]
        try:
            return RealtimeTopic(topic)
        except ValueError:
            return topic
