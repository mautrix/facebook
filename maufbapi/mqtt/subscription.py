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


_topic_map: Dict[str, str] = {
    "/messenger_sync_create_queue": "23",
    "/t_ms": "59",

    "/pp": "34",  # unknown
    "/ig_sub_iris": "134",
    "/ig_sub_iris_response": "135",
    "/ig_message_sync": "146",
    "/ig_send_message": "132",
    "/ig_send_message_response": "133",
    "/ig_realtime_sub": "149",
    "/pubsub": "88",
    "/t_fs": "102",  # Foreground state
    "/graphql": "9",
    "/t_region_hint": "150",
    "/mqtt_health_stats": "/mqtt_health_stats",
    "179": "179",  # also unknown
}

_reverse_topic_map: Dict[str, str] = {value: key for key, value in _topic_map.items()}


class RealtimeTopic(Enum):
    SYNC_CREATE_QUEUE = "/messenger_sync_create_queue"
    T_MS = "/t_ms"

    SUB_IRIS = "/ig_sub_iris"
    SUB_IRIS_RESPONSE = "/ig_sub_iris_response"
    MESSAGE_SYNC = "/ig_message_sync"
    SEND_MESSAGE = "/ig_send_message"
    SEND_MESSAGE_RESPONSE = "/ig_send_message_response"
    REALTIME_SUB = "/ig_realtime_sub"
    PUBSUB = "/pubsub"
    FOREGROUND_STATE = "/t_fs"
    GRAPHQL = "/graphql"
    REGION_HINT = "/t_region_hint"
    MQTT_HEALTH_STATS = "/mqtt_health_stats"
    UNKNOWN_PP = "/pp"
    UNKNOWN_179 = "179"

    @property
    def encoded(self) -> str:
        return _topic_map[self.value]

    @staticmethod
    def decode(val: str) -> 'RealtimeTopic':
        return RealtimeTopic(_reverse_topic_map[val])
