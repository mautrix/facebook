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
from typing import Dict, List

from attr import dataclass

from maufbapi.thrift import ThriftObject, TType, autospec, field


@autospec
@dataclass(kw_only=True)
class RealtimeClientInfo(ThriftObject):
    user_id: int = field(TType.I64)
    user_agent: str
    client_capabilities: int = field(TType.I64)
    endpoint_capabilities: int = field(TType.I64)
    # 0 = no zlib?, 1 = always zlib, 2 = optional zlib
    publish_format: int = field(TType.I32)
    no_automatic_foreground: bool
    make_user_available_in_foreground: bool
    device_id: str = ""
    is_initially_foreground: bool
    network_type: int = field(TType.I32)
    network_subtype: int = field(TType.I32)
    client_mqtt_session_id: int = field(TType.I64)
    client_ip_address: str = None
    subscribe_topics: List[int] = field(TType.LIST, item_type=TType.I32)
    client_type: str
    app_id: int = field(TType.I64)
    override_nectar_logging: bool = None
    connect_token_hash: bytes = None
    region_preference: str
    device_secret: str
    client_stack: int = field(TType.BYTE)
    fbns_connection_key: int = field(TType.I64, default=None)
    fbns_connection_secret: str = None
    fbns_device_id: str = None
    fbns_device_secret: str = None
    luid: int = field(TType.I64, default=None)
    network_type_info: int = field(TType.I32, default=None)
    # ssl_fingerprint: str = None
    # tcp_fingerprint: str = None


@autospec
@dataclass(kw_only=True)
class PHPOverride(ThriftObject):
    hostname: str = None
    port: int = field(TType.I32, default=0)
    host_ip_address: str = None


@autospec
@dataclass(kw_only=True)
class UnknownStruct(ThriftObject):
    pass


@autospec
@dataclass(kw_only=True)
class RealtimeConfig(ThriftObject):
    client_identifier: str
    will_topic: str = None
    will_message: str = None
    client_info: RealtimeClientInfo
    password: str = ""
    get_diffs_request: List[str] = None
    proxygen_info: List[UnknownStruct] = None
    combined_publishes: List[UnknownStruct] = None
    zero_rating_token_hash: str = None
    app_specific_info: Dict[str, str] = None
    php_override: PHPOverride = None
    # token_binding_message: ... = None


@autospec
@dataclass(kw_only=True)
class ForegroundStateConfig(ThriftObject):
    in_foreground_app: bool
    in_foreground_device: bool
    keep_alive_timeout: int = field(TType.I32)
    subscribe_topics: List[str]
    subscribe_generic_topics: List[str]
    unsubscribe_topics: List[str]
    unsubscribe_generic_topics: List[str]
    request_id: int = field(TType.I64)
