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

from typing import Any, Awaitable, Callable, Type, TypeVar
from collections import defaultdict
from socket import error as SocketError, socket
import asyncio
import json
import logging
import random
import time
import urllib.request
import zlib

from yarl import URL
import paho.mqtt.client as pmc

from mautrix.util.logging import TraceLogger

from ..state import AndroidState
from ..thrift import ThriftObject
from ..types import (
    MarkReadRequest,
    MessageSyncError,
    MessageSyncPayload,
    NTContext,
    PHPOverride,
    RealtimeClientInfo,
    RealtimeConfig,
    RegionHintPayload,
    ResumeQueueRequest,
    SendMessageRequest,
    SendMessageResponse,
    SetTypingRequest,
    TypingNotification,
)
from ..types.mqtt import Mention, Presence
from .events import Connect, Disconnect
from .otclient import MQTToTClient
from .subscription import RealtimeTopic, topic_map

try:
    import socks
except ImportError:
    socks = None

T = TypeVar("T")
no_prefix_topics = (RealtimeTopic.TYPING_NOTIFICATION, RealtimeTopic.ORCA_PRESENCE)


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
    seq_id: int | None
    seq_id_update_callback: Callable[[int], None] | None
    connect_token_hash: bytes | None
    region_hint_callback: Callable[[str], None] | None
    connection_unauthorized_callback: Callable[[], None] | None
    enable_web_presence: bool
    _opened_thread: int | None
    _publish_waiters: dict[int, asyncio.Future]
    _response_waiters: dict[RealtimeTopic, asyncio.Future]
    _response_waiter_locks: dict[RealtimeTopic, asyncio.Lock]
    _disconnect_error: Exception | None
    _event_handlers: dict[Type[T], list[Callable[[T], Awaitable[None]]]]
    _outgoing_events: asyncio.Queue
    _event_dispatcher_task: asyncio.Task | None

    # region Initialization

    def __init__(
        self,
        state: AndroidState,
        loop: asyncio.AbstractEventLoop | None = None,
        log: TraceLogger | None = None,
        connect_token_hash: bytes | None = None,
    ) -> None:
        self.seq_id = None
        self.seq_id_update_callback = None
        self.connect_token_hash = connect_token_hash
        self.region_hint_callback = None
        self.connection_unauthorized_callback = None
        self.enable_web_presence = False
        self._opened_thread = None
        self._publish_waiters = {}
        self._response_waiters = {}
        self._disconnect_error = None
        self._response_waiter_locks = defaultdict(lambda: asyncio.Lock())
        self._event_handlers = defaultdict(lambda: [])
        self._event_dispatcher_task = None
        self._outgoing_events = asyncio.Queue()
        self.log = log or logging.getLogger("mauigpapi.mqtt")
        self._loop = loop or asyncio.get_event_loop()
        self.state = state
        self._client = MQTToTClient(
            client_id=self._form_client_id(),
            clean_session=True,
            protocol=pmc.MQTTv31,
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
                self._client.proxy_set(
                    proxy_type=proxy_type,
                    proxy_addr=proxy_url.host,
                    proxy_port=proxy_url.port,
                    proxy_username=proxy_url.user,
                    proxy_password=proxy_url.password,
                )
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
        self._client.on_disconnect = self._on_disconnect_handler
        self._client.on_socket_open = self._on_socket_open
        self._client.on_socket_close = self._on_socket_close
        self._client.on_socket_register_write = self._on_socket_register_write
        self._client.on_socket_unregister_write = self._on_socket_unregister_write

    def _form_client_id(self, force_password: bool = False) -> bytes:
        subscribe_topics = [
            "/t_assist_rp",
            "/t_rtc",
            "/webrtc_response",
            "/t_rtc_log",
            RealtimeTopic.MESSAGE_SYNC,
            "/pp",
            "/webrtc",
            "/quick_promotion_refresh",
            "/t_omnistore_sync_low_pri",
            "/get_media_resp",
            "/t_dr_response",
            "/t_omnistore_sync",
            "/t_push",
            "/ixt_trigger",
            "/rs_resp",
            RealtimeTopic.REGION_HINT,
            "/t_trace",
            RealtimeTopic.TYPING_NOTIFICATION,
            "/sr_res",
            "/ls_resp",
            "/t_rtc_multi",
            # RealtimeTopic.SEND_MESSAGE_RESP,
            # RealtimeTopic.MARK_THREAD_READ_RESPONSE,
        ]

        if self.enable_web_presence:
            subscribe_topics.append(RealtimeTopic.ORCA_PRESENCE)

        topic_ids = [
            int(topic.encoded if isinstance(topic, RealtimeTopic) else topic_map[topic])
            for topic in subscribe_topics
        ]
        cfg = RealtimeConfig(
            client_identifier=self.state.device.uuid[:20],
            client_info=RealtimeClientInfo(
                user_id=self.state.session.uid,
                user_agent=self.state.user_agent_meta,
                client_capabilities=0b1100001110110111,
                endpoint_capabilities=0b1011010,
                publish_format=2,
                no_automatic_foreground=True,
                make_user_available_in_foreground=False,
                device_id=self.state.device.uuid,
                is_initially_foreground=True,
                network_type=1 if self.state.device.connection_type == "WIFI" else 0,
                network_subtype=0 if self.state.device.connection_type == "WIFI" else 13,
                client_mqtt_session_id=int(time.time() * 1000) & 0xFFFFFFFF,
                subscribe_topics=topic_ids,
                client_type="",
                app_id=int(self.state.application.client_id),
                region_preference=self.state.session.region_hint,
                device_secret="",
                client_stack=4,
                network_type_info=7 if self.state.device.connection_type == "WIFI" else 4,
            ),
            password=self.state.session.access_token,
            app_specific_info={
                "ls_sv": str(self.state.application.version_id),
                "ls_fdid": self.state.device.fdid,
            },
            combined_publishes=[],
            php_override=PHPOverride(),
        )
        if self.connect_token_hash:
            self.log.trace("Using connect_token_hash to connect %s", self.connect_token_hash)
            if not force_password:
                cfg.password = ""
            cfg.client_info.device_id = ""
            cfg.client_info.user_agent = self.state.minimal_user_agent_meta
            cfg.client_info.connect_token_hash = self.connect_token_hash
        else:
            self.log.trace("Making fresh connection")
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

    def _on_connect_handler(
        self, client: MQTToTClient, _: Any, flags: dict[str, Any], rc: int
    ) -> None:
        if rc != 0:
            if rc == pmc.MQTT_ERR_INVAL:
                self.log.error("MQTT connection error, regenerating client ID")
                # self.connect_token_hash = None
                self._client.set_client_id(self._form_client_id(force_password=True))
            else:
                err = pmc.connack_string(rc)
                self.log.error("MQTT Connection Error: %s (%d)", err, rc)
                if (
                    rc == pmc.CONNACK_REFUSED_NOT_AUTHORIZED
                    and self.connection_unauthorized_callback
                ):
                    self.connection_unauthorized_callback()
            return

        asyncio.create_task(self._post_connect())

    def _on_disconnect_handler(self, client: MQTToTClient, _: Any, rc: int) -> None:
        err_str = "Generic error." if rc == pmc.MQTT_ERR_NOMEM else pmc.error_string(rc)
        self.log.debug(f"MQTT disconnection code %d: %s", rc, err_str)

    @property
    def _sync_queue_params(self) -> dict[str, Any]:
        return {
            "client_delta_sync_bitmask": "CAvV/nxib6vRgAV/ss2A",
            "graphql_query_hashes": {"xma_query_id": "0"},
            "graphql_query_params": {
                "0": {
                    "xma_id": "<ID>",
                    "small_preview_width": 716,
                    "small_preview_height": 358,
                    "large_preview_width": 1500,
                    "large_preview_height": 750,
                    "full_screen_width": 4096,
                    "full_screen_height": 4096,
                    "blur": 0,
                    "nt_context": {
                        "styles_id": NTContext().styles_id,
                        "pixel_ratio": 3,
                    },
                    "use_oss_id": True,
                    "client_doc_id": "222672581515007895135860332111",
                }
            },
        }

    @property
    def _sync_create_queue_data(self) -> dict[str, Any]:
        return {
            "initial_titan_sequence_id": self.seq_id,
            "delta_batch_size": 125,
            "device_params": {
                "image_sizes": {
                    "0": "4096x4096",
                    "4": "358x358",
                    "1": "750x750",
                    "2": "481x481",
                    "3": "358x358",
                },
                "animated_image_format": "WEBP,GIF",
                "animated_image_sizes": {
                    "0": "4096x4096",
                    "4": "358x358",
                    "1": "750x750",
                    "2": "481x481",
                    "3": "358x358",
                },
                "thread_theme_background_sizes": {"0": "2048x2048"},
                "thread_theme_icon_sizes": {"1": "138x138", "3": "66x66"},
                "thread_theme_reaction_sizes": {"1": "83x83", "3": "39x39"},
            },
            "entity_fbid": self.state.session.uid,
            "sync_api_version": 10,
            "queue_params": self._sync_queue_params,
        }

    @property
    def _sync_resume_queue_data(self) -> ResumeQueueRequest:
        return ResumeQueueRequest(
            last_seq_id=self.seq_id,
            sync_api_version=10,
            queue_params=json.dumps(self._sync_queue_params, separators=(",", ":")),
        )

    async def _post_connect(self) -> None:
        self._opened_thread = None
        self.log.debug("Re-creating sync queue after reconnect")
        await self._dispatch(Connect())
        await self.publish(
            "/ls_req",
            {
                "label": "1",
                "payload": json.dumps(
                    {
                        "app_state": 1,
                        "request_id": "android_request_id",
                    }
                ),
                "version": str(self.state.application.version_id),
            },
        )
        if self.connect_token_hash is not None:
            await self.publish(
                RealtimeTopic.SYNC_RESUME_QUEUE, self._sync_resume_queue_data, prefix=b"\x00"
            )
        else:
            await self.publish(RealtimeTopic.SYNC_CREATE_QUEUE, self._sync_create_queue_data)

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
                self._outgoing_events.put_nowait(event)
        if parsed.items and not self._event_dispatcher_task:
            self._event_dispatcher_task = asyncio.create_task(self._dispatcher_loop())

    def _on_typing_notification(self, payload: bytes) -> None:
        try:
            parsed = TypingNotification.from_thrift(payload)
        except Exception:
            self.log.debug("Failed to parse typing notification %s", payload, exc_info=True)
            return
        asyncio.create_task(self._dispatch(parsed))

    def _on_presence(self, payload: bytes) -> None:
        try:
            presence = Presence.deserialize(json.loads(payload))
            asyncio.create_task(self._dispatch(presence))
        except Exception:
            self.log.debug("Failed to parse presence payload %s", payload, exc_info=True)
            return

    def _on_region_hint(self, payload: bytes) -> None:
        rhp = RegionHintPayload.from_thrift(payload)
        if self.region_hint_callback:
            self.region_hint_callback(rhp.region_hint.code)

    def _on_message_handler(self, client: MQTToTClient, _: Any, message: pmc.MQTTMessage) -> None:
        try:
            is_compressed = message.payload.startswith(b"x\xda")
            if is_compressed:
                message.payload = zlib.decompress(message.payload)
            if "#" in message.topic:
                topic_str, *rest = message.topic.split("#", 1)
            elif "/" in message.topic:
                topic_str, *rest = message.topic.split("/", 1)
            elif "|" in message.topic:
                topic_str, *rest = message.topic.split("|", 1)
            else:
                topic_str, rest = message.topic, []
            if len(rest) > 0:
                self.log.trace("Got extra data in topic %s: %s", topic_str, rest)
            topic = RealtimeTopic.decode(topic_str)
            if topic not in no_prefix_topics or message.payload.startswith(b"\x00"):
                _, message.payload = message.payload.split(b"\x00", 1)

            if topic == RealtimeTopic.MESSAGE_SYNC:
                self._on_message_sync(message.payload)
            elif topic == RealtimeTopic.TYPING_NOTIFICATION:
                self._on_typing_notification(message.payload)
            elif topic == RealtimeTopic.ORCA_PRESENCE:
                self._on_presence(message.payload)
            elif topic == RealtimeTopic.REGION_HINT:
                self._on_region_hint(message.payload)
            else:
                try:
                    waiter = self._response_waiters.pop(topic)
                except KeyError:
                    self.log.debug("No handler for MQTT message in %s: %s", topic, message.payload)
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
        except (SocketError, OSError, pmc.WebsocketConnectionError) as e:
            raise MQTTNotLoggedIn("MQTT reconnection failed") from e

    def add_event_handler(
        self, evt_type: Type[T], handler: Callable[[T], Awaitable[None]]
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

    async def _dispatcher_loop(self) -> None:
        loop_id = f"{hex(id(self))}#{time.monotonic()}"
        self.log.debug(f"Dispatcher loop {loop_id} starting")
        try:
            while True:
                evt = await self._outgoing_events.get()
                await asyncio.shield(self._dispatch(evt))
        except asyncio.CancelledError:
            tasks = self._outgoing_events
            self._outgoing_events = asyncio.Queue()
            if not tasks.empty():
                self.log.debug(
                    f"Dispatcher loop {loop_id} stopping after dispatching {tasks.qsize()} events"
                )
            while not tasks.empty():
                await self._dispatch(tasks.get_nowait())
            raise
        finally:
            self.log.debug(f"Dispatcher loop {loop_id} stopped")

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
            if self._client._state == pmc.mqtt_cs_disconnecting:
                break  # Stop listening

            if rc != pmc.MQTT_ERR_SUCCESS:
                # If known/expected error
                if rc == pmc.MQTT_ERR_CONN_LOST:
                    await self._dispatch(Disconnect(reason="Connection lost, retrying"))
                elif rc == pmc.MQTT_ERR_NOMEM:
                    # This error is wrongly classified
                    # See https://github.com/eclipse/paho.mqtt.python/issues/340
                    await self._dispatch(Disconnect(reason="Connection lost, retrying"))
                elif rc == pmc.MQTT_ERR_CONN_REFUSED:
                    raise MQTTNotLoggedIn("MQTT connection refused")
                elif rc == pmc.MQTT_ERR_NO_CONN:
                    if connection_retries > retry_limit:
                        raise MQTTNotConnected(f"Connection failed {connection_retries} times")
                    sleep = connection_retries * 2
                    msg = f"MQTT Error: no connection, retrying in {connection_retries} seconds"
                    await self._dispatch(Disconnect(reason=msg))
                    await asyncio.sleep(sleep)
                else:
                    err = pmc.error_string(rc)
                    self.log.error("MQTT Error: %s", err)
                    await self._dispatch(Disconnect(reason=f"MQTT Error: {err}, retrying"))

                await self._reconnect()
                connection_retries += 1
            else:
                connection_retries = 0
        if self._event_dispatcher_task:
            self._event_dispatcher_task.cancel()
            self._event_dispatcher_task = None
        if self._disconnect_error:
            self.log.info("disconnect_error is set, raising and clearing variable")
            err = self._disconnect_error
            self._disconnect_error = None
            raise err

    # region Basic outgoing MQTT

    def publish(
        self,
        topic: RealtimeTopic | str,
        payload: str | bytes | dict | ThriftObject,
        prefix: bytes = b"",
        compress: bool = True,
    ) -> asyncio.Future:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if isinstance(payload, ThriftObject):
            payload = payload.to_thrift()
        if compress:
            payload = zlib.compress(prefix + payload, level=9)
        elif prefix:
            payload = prefix + payload
        info = self._client.publish(
            topic.encoded if isinstance(topic, RealtimeTopic) else topic, payload, qos=1
        )
        fut = asyncio.Future()
        self._publish_waiters[info.mid] = fut
        return fut

    async def request(
        self,
        topic: RealtimeTopic,
        response: RealtimeTopic,
        payload: str | bytes | dict | ThriftObject,
        prefix: bytes = b"",
    ) -> pmc.MQTTMessage:
        async with self._response_waiter_locks[response]:
            fut = asyncio.Future()
            self._response_waiters[response] = fut
            await self.publish(topic, payload, prefix)
            return await fut

    @staticmethod
    def generate_offline_threading_id() -> int:
        rand = format(int(random.random() * 4294967295), "022b")[-22:]
        return int(f"{int(time.time() * 1000):b}{rand}", 2)

    async def send_message(
        self,
        target: int,
        is_group: bool,
        message: str = "",
        offline_threading_id: int | None = None,
        media_ids: list[int] = None,
        mentions: list[Mention] | None = None,
        reply_to: str | None = None,
    ) -> SendMessageResponse:
        if not offline_threading_id:
            offline_threading_id = self.generate_offline_threading_id()
        req = SendMessageRequest(
            chat_id=f"tfbid_{target}" if is_group else str(target),
            message=message,
            offline_threading_id=offline_threading_id,
            sender_fbid=self.state.session.uid,
            reply_to=reply_to,
            media_ids=[str(i) for i in media_ids] if media_ids else None,
            client_tags={"is_in_chatheads": "false", "trigger": "2:thread_list:thread"},
            msg_attempt_id=self.generate_offline_threading_id(),
        )
        if mentions:
            req.extra_metadata = {
                "prng": json.dumps(
                    [mention.serialize() for mention in mentions], separators=(",", ":")
                )
            }
        await self.opened_thread(target)
        self.log.trace("Send message request: %s", req)
        resp = await self.request(
            RealtimeTopic.SEND_MESSAGE,
            RealtimeTopic.SEND_MESSAGE_RESP,
            req,
            prefix=b"\x18\x00\x00",
        )
        self.log.trace("Send message response: %s", repr(resp.payload))
        return SendMessageResponse.from_thrift(resp.payload)

    async def opened_thread(self, target: int) -> None:
        if self._opened_thread == target:
            return
        self._opened_thread = target
        # req = OpenedThreadRequest()
        # req.chat_id = target
        # self.log.trace("Opened thread request: %s", req)
        # await self.publish(RealtimeTopic.OPENED_THREAD, req)

    async def mark_read(
        self, target: int, is_group: bool, read_to: int, offline_threading_id: int | None = None
    ) -> None:
        if not offline_threading_id:
            offline_threading_id = self.generate_offline_threading_id()
        req = MarkReadRequest(read_to=read_to, offline_threading_id=offline_threading_id)
        if is_group:
            req.group_id = target
        else:
            req.user_id = target
        await self.opened_thread(target)
        self.log.trace("Mark read request: %s", req)
        # resp = await self.request(
        #     RealtimeTopic.MARK_THREAD_READ,
        #     RealtimeTopic.MARK_THREAD_READ_RESPONSE,
        #     req,
        #     prefix=b"\x00",
        # )
        # self.log.trace("Mark read response: %s", repr(resp.payload))
        await self.publish(RealtimeTopic.MARK_THREAD_READ, req, prefix=b"\x00")

    async def set_typing(self, target: int, typing: bool = True) -> None:
        req = SetTypingRequest(
            user_id=target, own_id=self.state.session.uid, typing_status=int(typing)
        )
        await self.publish(RealtimeTopic.SET_TYPING, req, prefix=b"\x00")

    # endregion
