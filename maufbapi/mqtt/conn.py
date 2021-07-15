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
from typing import Union, Optional, Any, Dict, Awaitable, Type, List, TypeVar, Callable
from collections import defaultdict
from socket import socket, error as SocketError
import urllib.request
import logging
import asyncio
import random
import zlib
import time
import json

import paho.mqtt.client
from paho.mqtt.client import MQTTMessage, WebsocketConnectionError
from yarl import URL
from mautrix.util.logging import TraceLogger

from ..state import AndroidState
from ..types import (MessageSyncPayload, RealtimeConfig, RealtimeClientInfo, SendMessageRequest,
                     MarkReadRequest, OpenedThreadRequest, SendMessageResponse, RegionHintPayload)
from ..types.mqtt import Mention
from ..thrift import ThriftReader, ThriftObject
from .otclient import MQTToTClient
from .subscription import RealtimeTopic, topic_map
from .events import Connect, Disconnect

try:
    import socks
except ImportError:
    socks = None

T = TypeVar('T')


# TODO add some custom stuff in these?
class MQTTNotLoggedIn(Exception):
    pass


class MQTTNotConnected(Exception):
    pass


class AndroidMQTT:
    _loop: asyncio.AbstractEventLoop
    _client: MQTToTClient
    log: TraceLogger
    state: AndroidState
    seq_id: Optional[int]
    seq_id_update_callback: Optional[Callable[[int], None]]
    region_hint_callback: Optional[Callable[[str], None]]
    _opened_thread: Optional[int]
    _publish_waiters: Dict[int, asyncio.Future]
    _response_waiters: Dict[RealtimeTopic, asyncio.Future]
    _response_waiter_locks: Dict[RealtimeTopic, asyncio.Lock]
    _disconnect_error: Optional[Exception]
    _event_handlers: Dict[Type[T], List[Callable[[T], Awaitable[None]]]]

    # region Initialization

    def __init__(self, state: AndroidState, loop: Optional[asyncio.AbstractEventLoop] = None,
                 log: Optional[TraceLogger] = None) -> None:
        self.seq_id = None
        self.seq_id_update_callback = None
        self.region_hint_callback = None
        self._opened_thread = None
        self._publish_waiters = {}
        self._response_waiters = {}
        self._disconnect_error = None
        self._response_waiter_locks = defaultdict(lambda: asyncio.Lock())
        self._event_handlers = defaultdict(lambda: [])
        self.log = log or logging.getLogger("mauigpapi.mqtt")
        self._loop = loop or asyncio.get_event_loop()
        self.state = state
        self._client = MQTToTClient(
            client_id=self._form_client_id(),
            clean_session=True,
            protocol=paho.mqtt.client.MQTTv31,
            transport="tcp",
        )
        try:
            http_proxy = urllib.request.getproxies()["http"]
        except KeyError:
            http_proxy = None
        else:
            if not socks:
                self.log.warning("http_proxy is set, but pysocks is not installed")
            else:
                proxy_url = URL(http_proxy)
                proxy_type = {
                    "http": socks.HTTP,
                    "https": socks.HTTP,
                    "socks": socks.SOCKS5,
                    "socks5": socks.SOCKS5,
                    "socks4": socks.SOCKS4,
                }[proxy_url.scheme]
                self._client.proxy_set(proxy_type=proxy_type, proxy_addr=proxy_url.host,
                                       proxy_port=proxy_url.port, proxy_username=proxy_url.user,
                                       proxy_password=proxy_url.password)
        self._client.enable_logger()
        self._client.tls_set()
        # mqtt.max_inflight_messages_set(20)  # The rest will get queued
        # mqtt.max_queued_messages_set(0)  # Unlimited messages can be queued
        # mqtt.message_retry_set(20)  # Retry sending for at least 20 seconds
        # mqtt.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.connect_async("edge-mqtt.facebook.com", 443, keepalive=60)
        self._client.on_message = self._on_message_handler
        self._client.on_publish = self._on_publish_handler
        self._client.on_connect = self._on_connect_handler
        # self._client.on_disconnect = self._on_disconnect_handler
        self._client.on_socket_open = self._on_socket_open
        self._client.on_socket_close = self._on_socket_close
        self._client.on_socket_register_write = self._on_socket_register_write
        self._client.on_socket_unregister_write = self._on_socket_unregister_write

    def _form_client_id(self) -> bytes:
        # subscribe_topics = ["/t_p", "/t_assist_rp", "/t_rtc", "/webrtc_response",
        #                     RealtimeTopic.MESSAGE_SYNC, "/pp", "/webrtc",
        #                     "/quick_promotion_refresh", "/t_omnistore_sync_low_pri",
        #                     "/get_media_resp", "/t_dr_response", "/t_omnistore_sync", "/t_push",
        #                     "/t_thread_typing", "/ixt_trigger", "/rs_resp",
        #                     RealtimeTopic.REGION_HINT, "/t_tn", "/sr_res", "/t_tp", "/t_sp",
        #                     "/ls_resp", "/t_rtc_multi", RealtimeTopic.SEND_MESSAGE_RESP,
        #                     RealtimeTopic.MARK_THREAD_READ_RESPONSE,
        #                     ]
        subscribe_topics = [RealtimeTopic.MESSAGE_SYNC, RealtimeTopic.REGION_HINT,
                            RealtimeTopic.SEND_MESSAGE_RESP,
                            RealtimeTopic.MARK_THREAD_READ_RESPONSE]
        topic_ids = [int(topic.encoded if isinstance(topic, RealtimeTopic) else topic_map[topic])
                     for topic in subscribe_topics]
        cfg = RealtimeConfig(
            client_identifier=self.state.device.uuid[:20],
            client_info=RealtimeClientInfo(
                user_id=self.state.session.uid,
                user_agent=self.state.user_agent_meta,
                client_capabilities=0b100001110110111,
                endpoint_capabilities=0b1011010,
                publish_format=1,
                no_automatic_foreground=True,
                make_user_available_in_foreground=True,
                device_id=self.state.device.uuid,
                is_initially_foreground=True,
                network_type=1,
                network_subtype=0,
                client_mqtt_session_id=int(time.time() * 1000) & 0xffffffff,
                subscribe_topics=topic_ids,
                client_type="",
                app_id=int(self.state.application.client_id),
                region_preference=self.state.session.region_hint or "ODN",
                device_secret="",
                client_stack=4,
                yet_another_unknown=7,
            ),
            password=self.state.session.access_token,
            app_specific_info={
                "ls_sv": str(self.state.application.version_id),
                "ls_fdid": self.state.device.uuid,
            },
        )
        return zlib.compress(cfg.to_thrift(), level=9)

    # endregion

    def _on_socket_open(self, client: MQTToTClient, _: Any, sock: socket) -> None:
        self._loop.add_reader(sock, client.loop_read)

    def _on_socket_close(self, client: MQTToTClient, _: Any, sock: socket) -> None:
        self._loop.remove_reader(sock)

    def _on_socket_register_write(self, client: MQTToTClient, _: Any, sock: socket) -> None:
        self._loop.add_writer(sock, client.loop_write)

    def _on_socket_unregister_write(self, client: MQTToTClient, _: Any, sock: socket) -> None:
        self._loop.remove_writer(sock)

    def _on_connect_handler(self, client: MQTToTClient, _: Any, flags: Dict[str, Any], rc: int
                            ) -> None:
        if rc != 0:
            if rc == paho.mqtt.client.MQTT_ERR_INVAL:
                self.log.error("MQTT connection error, regenerating client ID")
                self._client.set_client_id(self._form_client_id())
            else:
                err = paho.mqtt.client.connack_string(rc)
                self.log.error("MQTT Connection Error: %s (%d)", err, rc)
            return

        asyncio.create_task(self._post_connect())

    async def _post_connect(self) -> None:
        self._opened_thread = None
        self.log.debug("Re-creating sync queue after reconnect")
        await self._dispatch(Connect())
        await self.publish("/ls_req", {
            "label": "1",
            "payload": json.dumps({
                "app_state": 1,
                "request_id": "android_request_id",
            }),
            "version": str(self.state.application.version_id),
        })
        await self.publish(RealtimeTopic.SYNC_CREATE_QUEUE, {
            "initial_titan_sequence_id": self.seq_id,
            "delta_batch_size": 125,
            "device_params": {
                "image_sizes": {
                    "0": "4096x4096",
                    "4": "358x358",
                    "1": "750x750",
                    "2": "481x481",
                    "3": "358x358"
                },
                "animated_image_format": "WEBP,GIF",
                "animated_image_sizes": {
                    "0": "4096x4096",
                    "4": "358x358",
                    "1": "750x750",
                    "2": "481x481",
                    "3": "358x358"
                },
                "thread_theme_background_sizes": {
                    "0": "2048x2048"
                },
                "thread_theme_icon_sizes": {
                    "1": "138x138",
                    "3": "66x66"
                },
                "thread_theme_reaction_sizes": {
                    "1": "83x83",
                    "3": "39x39"
                }
            },
            "entity_fbid": self.state.session.uid,
            "sync_api_version": 10,
            "queue_params": {
                "client_delta_sync_bitmask": "B/p8Ym/r2YAFf7PNgA",
                "graphql_query_hashes": {
                    "xma_query_id": "3257579454369025"
                },
                "graphql_query_params": {
                    "3257579454369025": {
                        "xma_id": "<ID>",
                        "small_preview_width": 716,
                        "small_preview_height": 358,
                        "large_preview_width": 1500,
                        "large_preview_height": 750,
                        "full_screen_width": 4096,
                        "full_screen_height": 4096,
                        "blur": 0,
                        "nt_context": {
                            "styles_id": "7d328425a4dfa3aa76b1310fa8dc30bf",
                            "pixel_ratio": 3
                        },
                        "use_oss_id": True
                    }
                }
            }
        })

    def _on_publish_handler(self, client: MQTToTClient, _: Any, mid: int) -> None:
        try:
            waiter = self._publish_waiters[mid]
        except KeyError:
            return
        waiter.set_result(None)

    # region Incoming event parsing

    def _update_seq_id(self, msp: MessageSyncPayload) -> None:
        if msp.last_seq_id and msp.last_seq_id > self.seq_id:
            self.seq_id = msp.last_seq_id
            self.seq_id_update_callback(self.seq_id)

    def _on_message_sync(self, payload: bytes) -> None:
        try:
            parsed = MessageSyncPayload.from_thrift(payload)
        except Exception:
            self.log.debug("Failed to parse message sync payload %s", payload, exc_info=True)
            return
        self._update_seq_id(parsed)
        if parsed.error:
            asyncio.create_task(self._dispatch(parsed.error))
        for item in parsed.items:
            for event in item.get_parts():
                asyncio.create_task(self._dispatch(event))

    def _on_region_hint(self, payload: bytes) -> None:
        rhp = RegionHintPayload.from_thrift(payload)
        if self.region_hint_callback:
            self.region_hint_callback(rhp.region_hint.code)

    def _on_message_handler(self, client: MQTToTClient, _: Any, message: MQTTMessage) -> None:
        try:
            is_compressed = message.payload.startswith(b"x\xda")
            if is_compressed:
                message.payload = zlib.decompress(message.payload)
            topic = RealtimeTopic.decode(message.topic)
            _, message.payload = message.payload.split(b"\x00", 1)
            if topic == RealtimeTopic.MESSAGE_SYNC:
                self._on_message_sync(message.payload)
            elif topic == RealtimeTopic.REGION_HINT:
                self._on_region_hint(message.payload)
            else:
                try:
                    waiter = self._response_waiters.pop(topic)
                except KeyError:
                    self.log.debug("No handler for MQTT message in %s: %s",
                                   topic, message.payload)
                    ThriftReader(message.payload).pretty_print()
                else:
                    waiter.set_result(message)
        except Exception:
            self.log.exception("Error in incoming MQTT message handler")
            self.log.trace("Errored MQTT payload: %s", message.payload)

    # endregion

    async def _reconnect(self) -> None:
        try:
            self.log.trace("Trying to reconnect to MQTT")
            self._client.reconnect()
        except (SocketError, OSError, WebsocketConnectionError) as e:
            raise MQTTNotLoggedIn("MQTT reconnection failed") from e

    def add_event_handler(self, evt_type: Type[T], handler: Callable[[T], Awaitable[None]]
                          ) -> None:
        self._event_handlers[evt_type].append(handler)

    async def _dispatch(self, evt: T) -> None:
        for handler in self._event_handlers[type(evt)]:
            self.log.trace("Dispatching event %s", evt)
            try:
                await handler(evt)
            except Exception:
                self.log.exception(f"Error in {type(evt).__name__} handler")

    def disconnect(self) -> None:
        self._client.disconnect()

    async def listen(self, seq_id: int, retry_limit: int = 5) -> None:
        self.seq_id = seq_id

        self.log.debug("Connecting to Messenger MQTT")
        await self._reconnect()
        connection_retries = 0

        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.disconnect()
                # this might not be necessary
                self._client.loop_misc()
                break
            rc = self._client.loop_misc()

            # If disconnect() has been called
            # Beware, internal API, may have to change this to something more stable!
            if self._client._state == paho.mqtt.client.mqtt_cs_disconnecting:
                break  # Stop listening

            if rc != paho.mqtt.client.MQTT_ERR_SUCCESS:
                # If known/expected error
                if rc == paho.mqtt.client.MQTT_ERR_CONN_LOST:
                    await self._dispatch(Disconnect(reason="Connection lost, retrying"))
                elif rc == paho.mqtt.client.MQTT_ERR_NOMEM:
                    # This error is wrongly classified
                    # See https://github.com/eclipse/paho.mqtt.python/issues/340
                    await self._dispatch(Disconnect(reason="Connection lost, retrying"))
                elif rc == paho.mqtt.client.MQTT_ERR_CONN_REFUSED:
                    raise MQTTNotLoggedIn("MQTT connection refused")
                elif rc == paho.mqtt.client.MQTT_ERR_NO_CONN:
                    if connection_retries > retry_limit:
                        raise MQTTNotConnected(f"Connection failed {connection_retries} times")
                    sleep = connection_retries * 2
                    await self._dispatch(Disconnect(reason="MQTT Error: no connection, retrying "
                                                           f"in {connection_retries} seconds"))
                    await asyncio.sleep(sleep)
                else:
                    err = paho.mqtt.client.error_string(rc)
                    self.log.error("MQTT Error: %s", err)
                    await self._dispatch(Disconnect(reason=f"MQTT Error: {err}, retrying"))

                await self._reconnect()
                connection_retries += 1
            else:
                connection_retries = 0
        if self._disconnect_error:
            self.log.info("disconnect_error is set, raising and clearing variable")
            err = self._disconnect_error
            self._disconnect_error = None
            raise err

    # region Basic outgoing MQTT

    def publish(self, topic: Union[RealtimeTopic, str],
                payload: Union[str, bytes, dict, ThriftObject],
                prefix: bytes = b"") -> asyncio.Future:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if isinstance(payload, ThriftObject):
            payload = payload.to_thrift()
        payload = zlib.compress(prefix + payload, level=9)
        info = self._client.publish(topic.encoded if isinstance(topic, RealtimeTopic) else topic,
                                    payload, qos=1)
        fut = asyncio.Future()
        self._publish_waiters[info.mid] = fut
        return fut

    async def request(self, topic: RealtimeTopic, response: RealtimeTopic,
                      payload: Union[str, bytes, dict, ThriftObject], prefix: bytes = b""
                      ) -> MQTTMessage:
        async with self._response_waiter_locks[response]:
            fut = asyncio.Future()
            self._response_waiters[response] = fut
            await self.publish(topic, payload, prefix)
            return await fut

    @staticmethod
    def generate_offline_threading_id() -> int:
        rand = format(int(random.random() * 4294967295), "022b")[-22:]
        return int(f"{int(time.time() * 1000):b}{rand}", 2)

    async def send_message(self, target: int, is_group: bool, message: str = "",
                           offline_threading_id: Optional[int] = None, media_ids: List[int] = None,
                           mentions: Optional[List[Mention]] = None, reply_to: Optional[str] = None
                           ) -> SendMessageResponse:
        if not offline_threading_id:
            offline_threading_id = self.generate_offline_threading_id()
        req = SendMessageRequest(chat_id=f"tfbid_{target}" if is_group else str(target),
                                 message=message, offline_threading_id=offline_threading_id,
                                 sender_id=self.state.session.uid, reply_to=reply_to,
                                 media_ids=[str(i) for i in media_ids] if media_ids else None,
                                 flags={"is_in_chatheads": "false",
                                        "trigger": "2:thread_list:thread"},
                                 tid2=self.generate_offline_threading_id())
        if mentions:
            req.extra_metadata = {"prng": json.dumps([mention.serialize() for mention in mentions],
                                                     separators=(',', ':'))}
        await self.opened_thread(target)
        self.log.trace("Send message request: %s", req)
        resp = await self.request(RealtimeTopic.SEND_MESSAGE, RealtimeTopic.SEND_MESSAGE_RESP, req,
                                  prefix=b"\x18\x00\x00")
        self.log.trace("Send message response: %s", repr(resp.payload))
        return SendMessageResponse.from_thrift(resp.payload)

    async def opened_thread(self, target: int) -> None:
        if self._opened_thread == target:
            return
        self._opened_thread = target
        req = OpenedThreadRequest()
        req.chat_id = target
        self.log.trace("Opened thread request: %s", req)
        await self.publish(RealtimeTopic.OPENED_THREAD, req)

    async def mark_read(self, target: int, is_group: bool, read_to: int,
                        offline_threading_id: Optional[int] = None) -> None:
        if not offline_threading_id:
            offline_threading_id = self.generate_offline_threading_id()
        req = MarkReadRequest(read_to=read_to, offline_threading_id=offline_threading_id)
        if is_group:
            req.group_id = target
        else:
            req.user_id = target
        await self.opened_thread(target)
        self.log.trace("Mark read request: %s", req)
        resp = await self.request(RealtimeTopic.MARK_THREAD_READ,
                                  RealtimeTopic.MARK_THREAD_READ_RESPONSE,
                                  req, prefix=b"\x00")
        self.log.trace("Mark read response: %s", repr(resp.payload))

    # endregion
