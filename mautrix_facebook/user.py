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

from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterable, Awaitable, Callable, TypeVar, cast
from datetime import datetime, timedelta
from functools import partial
import asyncio
import base64
import hashlib
import hmac
import re
import time

from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.http import InvalidAccessToken, ResponseError
from maufbapi.mqtt import (
    Connect,
    Disconnect,
    MQTTNotConnected,
    MQTTNotLoggedIn,
    MQTTReconnectionError,
    ProxyUpdate,
)
from maufbapi.types import graphql, mqtt as mqtt_t
from maufbapi.types.graphql.responses import Message, Thread
from mautrix.bridge import BaseUser, async_getter_lock
from mautrix.errors import MNotFound
from mautrix.types import (
    EventID,
    MessageType,
    PresenceState,
    PushActionType,
    PushRuleKind,
    PushRuleScope,
    RoomID,
    TextMessageEventContent,
    UserID,
)
from mautrix.util import background_task
from mautrix.util.bridge_state import BridgeState, BridgeStateEvent
from mautrix.util.opt_prometheus import Gauge, Summary, async_time
from mautrix.util.proxy import RETRYABLE_PROXY_EXCEPTIONS, ProxyHandler
from mautrix.util.simple_lock import SimpleLock

from . import portal as po, puppet as pu
from .commands import enter_2fa_code
from .config import Config
from .db import Backfill, Message as DBMessage, ThreadType, User as DBUser, UserPortal
from .presence import PresenceUpdater
from .util.interval import get_interval

METRIC_SYNC_THREADS = Summary("bridge_sync_threads", "calls to sync_threads")
METRIC_RESYNC = Summary("bridge_on_resync", "calls to on_resync")
METRIC_UNKNOWN_EVENT = Summary("bridge_on_unknown_event", "calls to on_unknown_event")
METRIC_MEMBERS_ADDED = Summary("bridge_on_members_added", "calls to on_members_added")
METRIC_MEMBER_REMOVED = Summary("bridge_on_member_removed", "calls to on_member_removed")
METRIC_TYPING = Summary("bridge_on_typing", "calls to on_typing")
METRIC_PRESENCE = Summary("bridge_on_presence", "calls to on_presence")
METRIC_REACTION = Summary("bridge_on_reaction", "calls to on_reaction")
METRIC_FORCED_FETCH = Summary("bridge_on_forced_fetch", "calls to on_forced_fetch")
METRIC_MESSAGE_UNSENT = Summary("bridge_on_unsent", "calls to on_unsent")
METRIC_DELTA_RTC_MULTIWAY_MESSAGE = Summary(
    "bridge_on_metric_delta_rtc_multiway_message", "calls to on_delta_rtc_multiway_message"
)
METRIC_MESSAGE_SEEN = Summary("bridge_on_message_seen", "calls to on_message_seen")
METRIC_TITLE_CHANGE = Summary("bridge_on_title_change", "calls to on_title_change")
METRIC_AVATAR_CHANGE = Summary("bridge_on_avatar_change", "calls to on_avatar_change")
METRIC_THREAD_CHANGE = Summary("bridge_on_thread_change", "calls to on_thread_change")
METRIC_MESSAGE = Summary("bridge_on_message", "calls to on_message")
METRIC_LOGGED_IN = Gauge("bridge_logged_in", "Users logged into the bridge")
METRIC_CONNECTED = Gauge("bridge_connected", "Bridge users connected to Facebook")

if TYPE_CHECKING:
    from .__main__ import MessengerBridge

try:
    from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
except ImportError:

    class ProxyError(Exception):
        pass

    ProxyConnectionError = ProxyTimeoutError = ProxyError

T = TypeVar("T")

BridgeState.human_readable_errors.update(
    {
        "fb-reconnection-error": "Failed to reconnect to Messenger",
        "fb-connection-error": "Messenger disconnected unexpectedly",
        "fb-auth-error": "Authentication error from Messenger: {message}",
        "fb-disconnected": None,
        "fb-no-mqtt": "You're not connected to Messenger",
        "logged-out": "You're not logged into Messenger",
    }
)


class User(DBUser, BaseUser):
    temp_disconnect_notices: bool = True
    shutdown: bool = False
    config: Config

    by_mxid: dict[UserID, User] = {}
    by_fbid: dict[int, User] = {}

    client: AndroidAPI | None
    mqtt: AndroidMQTT | None
    listen_task: asyncio.Task | None
    _backfill_loop_task: asyncio.Task | None
    _thread_sync_task: asyncio.Task | None
    seq_id: int | None

    _notice_room_lock: asyncio.Lock
    _notice_send_lock: asyncio.Lock
    is_admin: bool
    permission_level: str
    _is_logged_in: bool | None
    _is_connected: bool | None
    _connection_time: float
    _prev_thread_sync: float
    _prev_reconnect_fail_refresh: float
    _db_instance: DBUser | None
    _sync_lock: SimpleLock
    _is_refreshing: bool
    _logged_in_info: graphql.LoggedInUser | None
    _logged_in_info_time: float
    _last_seq_id_save: float
    _seq_id_save_task: asyncio.Task | None

    def __init__(
        self,
        mxid: UserID,
        fbid: int | None = None,
        state: AndroidState | None = None,
        notice_room: RoomID | None = None,
        seq_id: int | None = None,
        connect_token_hash: bytes | None = None,
        oldest_backfilled_thread_ts: int | None = None,
        total_backfilled_portals: int | None = None,
        thread_sync_completed: bool = False,
    ) -> None:
        super().__init__(
            mxid=mxid,
            fbid=fbid,
            state=state,
            notice_room=notice_room,
            seq_id=seq_id,
            connect_token_hash=connect_token_hash,
            oldest_backfilled_thread_ts=oldest_backfilled_thread_ts,
            total_backfilled_portals=total_backfilled_portals,
            thread_sync_completed=thread_sync_completed,
        )
        BaseUser.__init__(self)
        self.notice_room = notice_room
        self._notice_room_lock = asyncio.Lock()
        self._notice_send_lock = asyncio.Lock()
        self.command_status = None
        (
            self.relay_whitelisted,
            self.is_whitelisted,
            self.is_admin,
            self.permission_level,
        ) = self.config.get_permissions(mxid)
        self._is_logged_in = None
        self._is_connected = None
        self._connection_time = time.monotonic()
        self._prev_thread_sync = -10
        self._prev_reconnect_fail_refresh = time.monotonic()
        self._thread_sync_task = None
        self._backfill_loop_task = None
        self._sync_lock = SimpleLock(
            "Waiting for thread sync to finish before handling %s", log=self.log
        )
        self._is_refreshing = False
        self._logged_in_info = None
        self._logged_in_info_time = 0
        self._last_seq_id_save = 0
        self._seq_id_save_task = None

        self.client = None
        self.mqtt = None
        self.listen_task = None

        self.proxy_handler = ProxyHandler(
            api_url=self.config["bridge.get_proxy_api_url"],
        )

    @classmethod
    def init_cls(cls, bridge: "MessengerBridge") -> AsyncIterable[Awaitable[bool]]:
        cls.bridge = bridge
        cls.config = bridge.config
        cls.az = bridge.az
        cls.loop = bridge.loop
        cls.temp_disconnect_notices = bridge.config["bridge.temporary_disconnect_notices"]
        return (user.reload_session(is_startup=True) async for user in cls.all_logged_in())

    @property
    def is_connected(self) -> bool | None:
        return self._is_connected

    @is_connected.setter
    def is_connected(self, val: bool | None) -> None:
        if self._is_connected != val:
            self._is_connected = val
            self._connection_time = time.monotonic()

    @property
    def connection_time(self) -> float:
        return self._connection_time

    # region Database getters

    def _add_to_cache(self) -> None:
        self.by_mxid[self.mxid] = self
        if self.fbid:
            self.by_fbid[self.fbid] = self

    @classmethod
    async def all_logged_in(cls) -> AsyncGenerator["User", None]:
        users = await super().all_logged_in()
        user: cls
        for user in users:
            try:
                yield cls.by_mxid[user.mxid]
            except KeyError:
                user._add_to_cache()
                yield user

    @classmethod
    @async_getter_lock
    async def get_by_mxid(cls, mxid: UserID, *, create: bool = True) -> User | None:
        if pu.Puppet.get_id_from_mxid(mxid) or mxid == cls.az.bot_mxid:
            return None
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        user = cast(cls, await super().get_by_mxid(mxid))
        if user is not None:
            user._add_to_cache()
            return user

        if create:
            cls.log.debug(f"Creating user instance for {mxid}")
            user = cls(mxid)
            await user.insert()
            user._add_to_cache()
            return user

        return None

    @classmethod
    @async_getter_lock
    async def get_by_fbid(cls, fbid: int) -> User | None:
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            pass

        user = cast(cls, await super().get_by_fbid(fbid))
        if user is not None:
            user._add_to_cache()
            return user

        return None

    # endregion

    def generate_state(self) -> AndroidState:
        state = AndroidState()
        state.session.region_hint = self.config["facebook.default_region_hint"]
        state.device.connection_type = self.config["facebook.connection_type"]
        state.carrier.name = self.config["facebook.carrier"]
        state.carrier.hni = self.config["facebook.hni"]
        seed = hmac.new(
            key=self.config["facebook.device_seed"].encode("utf-8"),
            msg=self.mxid.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        state.generate(seed)
        return state

    async def get_own_info(self) -> graphql.LoggedInUser:
        if not self._logged_in_info or self._logged_in_info_time + 60 * 60 < time.monotonic():
            self._logged_in_info = await self.client.fetch_logged_in_user()
            self._logged_in_info_time = time.monotonic()
        return self._logged_in_info

    async def _load_session(self, is_startup: bool) -> bool:
        if self._is_logged_in and is_startup:
            return True
        elif not self.state:
            # If we have a user in the DB with no state, we can assume
            # FB logged us out and the bridge has restarted
            await self.push_bridge_state(BridgeStateEvent.BAD_CREDENTIALS, error="logged-out")
            return False
        self.state.device.connection_type = self.config["facebook.connection_type"]
        self.state.carrier.name = self.config["facebook.carrier"]
        self.state.carrier.hni = self.config["facebook.hni"]
        self.client = AndroidAPI(
            self.state,
            log=self.log.getChild("api"),
            proxy_handler=self.proxy_handler,
            on_proxy_update=self.on_proxy_update,
        )
        user_info = await self.fetch_logged_in_user()
        if user_info:
            self.log.info("Loaded session successfully")
            self._logged_in_info = user_info
            self._logged_in_info_time = time.monotonic()
            self._track_metric(METRIC_LOGGED_IN, True)
            self._is_logged_in = True
            self.is_connected = None
            self.stop_listen()
            self.stop_backfill_tasks()
            background_task.create(self.post_login(is_startup=is_startup))
            return True
        # Unset the client if we failed to fetch the user
        self.client = None
        return False

    async def _send_reset_notice(self, e: InvalidAccessToken, edit: EventID | None = None) -> None:
        await self.send_bridge_notice(
            "Got authentication error from Messenger:\n\n"
            f"> {e!s}\n\n"
            "If you changed your Facebook password or enabled two-factor authentication, this "
            "is normal and you just need to log in again.",
            edit=edit,
            important=True,
            state_event=BridgeStateEvent.BAD_CREDENTIALS,
            error_code="fb-auth-error",
            error_message=str(e),
        )
        await self.logout(remove_fbid=False, from_auth_error=True)

    async def fetch_logged_in_user(
        self,
        action: str = "restore session",
        refresh_proxy_on_failure: bool = False,
    ) -> None:
        attempt = 0
        while True:
            try:
                return await self.client.fetch_logged_in_user()
            except RETRYABLE_PROXY_EXCEPTIONS as e:
                # These are retried by the client up to 10 times, but we actually want to retry
                # these indefinitely so we capture them here again and retry.
                self.log.warning(
                    f"Proxy error fetching user from Faecbook: {e}, retrying in 1 minute",
                )
                await asyncio.sleep(60)
            except InvalidAccessToken as e:
                if action != "restore session":
                    await self._send_reset_notice(e)
                raise
            except ResponseError:
                if action != "restore session":
                    attempt += 1
                    wait = min(attempt * 30, 300)
                    self.log.warning(
                        f"Unknown response error while trying to {action}, "
                        f"retrying in {wait} seconds"
                    )
                    await self.push_bridge_state(
                        BridgeStateEvent.UNKNOWN_ERROR, error="fb-reconnection-error"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
            except Exception:
                self.log.exception(f"Failed to {action}")
                raise

    async def is_logged_in(self, _override: bool = False) -> bool:
        if not self.state or not self.client:
            return False
        if self._is_logged_in is None or _override:
            try:
                self._is_logged_in = bool(await self.get_own_info())
            except Exception:
                self.log.exception("Exception checking login status")
                self._is_logged_in = False
        return self._is_logged_in

    async def refresh(self, force_notice: bool = False) -> None:
        event_id = None
        self._is_refreshing = True
        if self.mqtt:
            self.log.debug("Disconnecting MQTT connection for session refresh...")
            if self.temp_disconnect_notices or force_notice:
                event_id = await self.send_bridge_notice(
                    "Disconnecting Messenger MQTT connection for session refresh...",
                    state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
                )
            self.mqtt.disconnect()
            if self.listen_task:
                try:
                    await asyncio.wait_for(self.listen_task, timeout=3)
                except asyncio.TimeoutError:
                    self.log.debug("Waiting for MQTT connection timed out")
                else:
                    self.log.debug("MQTT connection disconnected")
            self.mqtt = None
        if self.temp_disconnect_notices or force_notice:
            event_id = await self.send_bridge_notice(
                "Refreshing session...",
                edit=event_id,
                state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
            )
        self.client = None
        await self.reload_session(event_id)

    async def reload_session(
        self, event_id: EventID | None = None, retries: int = 3, is_startup: bool = False
    ) -> None:
        if is_startup:
            await self.push_bridge_state(BridgeStateEvent.CONNECTING)
        try:
            await self._load_session(is_startup=is_startup)
        except InvalidAccessToken as e:
            await self._send_reset_notice(e, edit=event_id)
        except ResponseError as e:
            will_retry = retries > 0
            retry = "Retrying in 1 minute" if will_retry else "Not retrying"
            notice = f"Failed to connect to Messenger: unknown response error {e}. {retry}"
            if will_retry:
                await self.send_bridge_notice(
                    notice,
                    edit=event_id,
                    state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
                )
                await asyncio.sleep(60)
                await self.reload_session(event_id, retries - 1)
            else:
                await self.send_bridge_notice(
                    notice,
                    edit=event_id,
                    important=True,
                    state_event=BridgeStateEvent.UNKNOWN_ERROR,
                    error_code="fb-reconnection-error",
                )
        except Exception:
            await self.send_bridge_notice(
                "Failed to connect to Messenger: unknown error (see logs for more details)",
                edit=event_id,
                state_event=BridgeStateEvent.UNKNOWN_ERROR,
                error_code="fb-reconnection-error",
            )
        finally:
            self._is_refreshing = False

    async def reconnect(self, fetch_user: bool = False, update_proxy: bool = False) -> None:
        self._is_refreshing = True
        if self.mqtt:
            self.mqtt.disconnect()
        await self.listen_task
        self.listen_task = None
        self.mqtt = None
        if update_proxy and self.proxy_handler.update_proxy_url(reason="reconnect"):
            await self.on_proxy_update()
        if fetch_user:
            self.log.debug("Fetching current user after MQTT disconnection")
            await self.fetch_logged_in_user(
                action="fetch current user after MQTT disconnection",
                refresh_proxy_on_failure=True,  # safe because MQTT is dropped
            )
        self.start_listen()
        self._is_refreshing = False

    async def logout(self, remove_fbid: bool = True, from_auth_error: bool = False) -> bool:
        ok = True
        self.stop_listen()
        self.stop_backfill_tasks()
        if self.state and self.client and not from_auth_error:
            try:
                ok = await self.client.logout()
            except Exception:
                self.log.warning("Error while sending logout request", exc_info=True)
        if remove_fbid:
            await self.push_bridge_state(BridgeStateEvent.LOGGED_OUT)
        self._track_metric(METRIC_LOGGED_IN, False)
        self.state = None
        self._is_logged_in = None
        self.is_connected = None
        self.client = None
        self.mqtt = None
        self.seq_id = None
        self.connect_token_hash = None
        self.total_backfilled_portals = None
        self.oldest_backfilled_thread_ts = None
        self.thread_sync_completed = False

        if self.fbid and remove_fbid:
            await UserPortal.delete_all(self.fbid)
            del self.by_fbid[self.fbid]
            self.fbid = None

            await Backfill.delete_all(self.mxid)

        await self.save()
        return ok

    async def post_login(self, is_startup: bool, from_login: bool = False) -> None:
        self.log.info(f"Running post-login actions ({is_startup=}, {from_login=}, {self.seq_id=})")
        self._add_to_cache()

        try:
            puppet = await pu.Puppet.get_by_fbid(self.fbid)

            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info("Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        # Backfill requests are handled synchronously so as not to overload the homeserver.
        # Users can configure their backfill stages to be more or less aggressive with backfilling
        # to try and avoid getting banned.
        if not self._backfill_loop_task or self._backfill_loop_task.done():
            self._backfill_loop_task = asyncio.create_task(self._handle_backfill_requests_loop())

        if not is_startup or not self.seq_id:
            await self.sync_recent_threads(from_login=from_login)
        else:
            self.start_listen()

        if self.config["bridge.backfill.enable"]:
            if self._thread_sync_task and not self._thread_sync_task.done():
                self.log.warning("Cancelling existing background thread sync task")
                self._thread_sync_task.cancel()
            self._thread_sync_task = asyncio.create_task(self.backfill_threads())

        if self.bridge.homeserver_software.is_hungry:
            self.log.info("Updating contact info for all users")
            asyncio.gather(*[puppet.update_contact_info() async for puppet in pu.Puppet.get_all()])

    async def _handle_backfill_requests_loop(self) -> None:
        if not self.config["bridge.backfill.enable"] or not self.config["bridge.backfill.msc2716"]:
            return

        while True:
            await self._sync_lock.wait("backfill request")
            req = await Backfill.get_next(self.mxid)
            if not req:
                await asyncio.sleep(30)
                continue
            self.log.info("Backfill request %s", req)
            try:
                portal = await po.Portal.get_by_fbid(
                    req.portal_fbid, fb_receiver=req.portal_fb_receiver
                )
                await req.mark_dispatched()
                await portal.backfill(self, req)
                await req.mark_done()
            except Exception as e:
                self.log.exception("Failed to backfill portal %s", req.portal_fbid)

                if isinstance(e, ResponseError):
                    self.log.warning("ResponseError: %s %s", e, str(e.data))

                # Don't try again to backfill this portal for a minute.
                await req.set_cooldown_timeout(60)

    async def get_direct_chats(self) -> dict[UserID, list[RoomID]]:
        return {
            pu.Puppet.get_mxid_from_id(portal.fbid): [portal.mxid]
            async for portal in po.Portal.get_all_by_receiver(self.fbid)
            if portal.mxid
        }

    async def run_with_sync_lock(self, func: Callable[[], Awaitable]):
        with self._sync_lock:
            retry_count = 0
            while retry_count < 5:
                try:
                    retry_count += 1
                    await func()

                    # The sync was successful. Exit the loop.
                    return
                except InvalidAccessToken as e:
                    await self.send_bridge_notice(
                        f"Got authentication error from Messenger:\n\n> {e!s}\n\n",
                        important=True,
                        state_event=BridgeStateEvent.BAD_CREDENTIALS,
                        error_code="fb-auth-error",
                        error_message=str(e),
                    )
                    await self.logout(remove_fbid=False, from_auth_error=True)
                    return
                except Exception:
                    self.log.exception(
                        "Failed to sync threads. Waiting 30 seconds before retrying sync."
                    )
                    await asyncio.sleep(30)

            # If we get here, it means that the sync has failed five times. If this happens, most
            # likely something very bad has happened.
            self.log.error("Failed to sync threads five times. Will not retry.")

    @async_time(METRIC_SYNC_THREADS)
    async def sync_recent_threads(self, from_login: bool = False):
        if (
            self._prev_thread_sync + 10 > time.monotonic()
            and self.mqtt
            and self.mqtt.seq_id is not None
        ):
            self.log.debug("Previous thread sync was less than 10 seconds ago, not re-syncing")
            self.start_listen()
            return
        self._prev_thread_sync = time.monotonic()

        await self.run_with_sync_lock(partial(self._sync_recent_threads, from_login))

    async def _sync_recent_threads(self, increment_total_backfilled_portals: bool = False):
        assert self.client
        sync_count = min(
            self.config["bridge.backfill.max_conversations"],
            self.config["bridge.max_startup_thread_sync_count"],
        )
        self.log.debug(f"Fetching {sync_count} threads, 20 at a time...")

        # We need to get the sequence ID before we start the listener task.
        resp = await self.client.fetch_thread_list()
        self.seq_id = int(resp.sync_sequence_id)
        thread_seq_ids = list(
            {int(thread.sync_sequence_id) for thread in resp.nodes if thread.sync_sequence_id}
        )
        if len(thread_seq_ids) > 1 or (
            len(thread_seq_ids) == 1 and thread_seq_ids[0] != self.seq_id
        ):
            self.seq_id = max(*thread_seq_ids, self.seq_id)
            self.log.warning(
                f"Got more than one sequence ID in thread list: primary={resp.sync_sequence_id}, "
                f"threads={thread_seq_ids}. Using highest value ({self.seq_id})"
            )
        if self.mqtt:
            self.mqtt.seq_id = self.seq_id
        self.log.debug(f"Got new seq_id {self.seq_id}")
        await self.save_seq_id()
        self.start_listen()

        local_limit: int | None = sync_count
        if sync_count == 0:
            return
        elif sync_count < 0:
            local_limit = None

        await self._sync_threads_with_delay(
            self.client.iter_thread_list(resp, local_limit=local_limit),
            stop_when_threads_have_no_messages_to_backfill=True,
            increment_total_backfilled_portals=increment_total_backfilled_portals,
            local_limit=local_limit,
        )

        await self.update_direct_chats()

    async def backfill_threads(self):
        try:
            await self.run_with_sync_lock(self._backfill_threads)
        except Exception:
            self.log.exception("Error in thread backfill loop")

    async def _backfill_threads(self):
        assert self.client
        if not self.config["bridge.backfill.enable"]:
            return

        max_conversations = self.config["bridge.backfill.max_conversations"] or 0
        if 0 <= max_conversations <= (self.total_backfilled_portals or 0):
            self.log.info("Backfill max_conversations count reached, not syncing any more portals")
            return
        elif self.thread_sync_completed:
            self.log.debug("Thread backfill is marked as completed, not syncing more portals")
            return
        local_limit = (
            max_conversations - (self.total_backfilled_portals or 0)
            if max_conversations >= 0
            else None
        )

        timestamp = self.oldest_backfilled_thread_ts or int(time.time() * 1000)
        backoff = self.config.get("bridge.backfill.backoff.thread_list", 300)
        await self._sync_threads_with_delay(
            self.client.iter_thread_list_from(
                timestamp,
                local_limit=local_limit,
                rate_limit_exceeded_backoff=backoff,
            ),
            increment_total_backfilled_portals=True,
            local_limit=local_limit,
        )
        await self.update_direct_chats()

    async def _sync_threads_with_delay(
        self,
        threads: AsyncIterable[Thread],
        increment_total_backfilled_portals: bool = False,
        stop_when_threads_have_no_messages_to_backfill: bool = False,
        local_limit: int | None = None,
    ):
        sync_delay = self.config["bridge.backfill.min_sync_thread_delay"]
        last_thread_sync_ts = 0.0
        found_thread_count = 0
        async for thread in threads:
            found_thread_count += 1
            now = time.monotonic()
            if last_thread_sync_ts is not None and now < last_thread_sync_ts + sync_delay:
                delay = last_thread_sync_ts + sync_delay - now
                self.log.debug("Thread sync is happening too quickly. Waiting for %ds", delay)
                await asyncio.sleep(delay)

            last_thread_sync_ts = time.monotonic()
            had_new_messages = await self._sync_thread(thread)
            if not had_new_messages and stop_when_threads_have_no_messages_to_backfill:
                self.log.debug("Got to threads with no new messages. Stopping sync.")
                return

            if increment_total_backfilled_portals:
                self.total_backfilled_portals = (self.total_backfilled_portals or 0) + 1
            self.oldest_backfilled_thread_ts = min(
                thread.updated_timestamp,
                self.oldest_backfilled_thread_ts or int(time.time() * 1000),
            )
            await self.save()
        if local_limit is None or found_thread_count < local_limit:
            if local_limit is None:
                self.log.info(
                    "Reached end of thread list with no limit, marking thread sync as completed"
                )
            else:
                self.log.info(
                    f"Reached end of thread list (got {found_thread_count} with "
                    f"limit {local_limit}), marking thread sync as completed"
                )
            self.thread_sync_completed = True
        await self.save()

    def _message_is_bridgable(self, message: Message) -> bool:
        for tag in message.tags_list:
            if tag.startswith("source:messenger_growth"):
                # This excludes messages like the following:
                # - X just joined messenger
                # - You are now connected on Messenger
                return False
            if tag == "source:titan:web":
                # Older "You are now connected on Messenger" messages
                return False
            if tag == "source:generic_admin_text":
                return False
        return True

    async def _sync_thread(self, thread: graphql.Thread) -> bool:
        """
        Sync a specific thread. Returns whether the thread had messages after the last message in
        the database before the sync.
        """
        self.log.debug(f"Syncing thread {thread.thread_key}")

        # If the thread only contains unbridgable messages, then don't create a portal for it.
        forward_messages = [m for m in thread.messages.nodes if self._message_is_bridgable(m)]
        if not forward_messages and not thread.messages.page_info.has_previous_page:
            self.log.debug(
                f"Thread {thread.thread_key} only contains unbridgable messages, skipping"
            )
            return False

        assert self.client
        portal = await po.Portal.get_by_thread(thread.thread_key, self.fbid)

        # Create or update the Matrix room
        was_created = False
        if not portal.mxid:
            await portal.create_matrix_room(self, thread)
            was_created = True
        else:
            await portal.update_matrix_room(self, thread)
        if was_created or not self.config["bridge.tag_only_on_create"]:
            await self.mute_room(portal, thread.mute_until)

        last_message = await DBMessage.get_most_recent(portal.fbid, portal.fb_receiver)
        if last_message:
            original_number_of_messages = len(forward_messages)
            new_messages = [m for m in forward_messages if last_message.timestamp < m.timestamp]
            forward_messages = new_messages

            portal.log.debug(
                f"{len(new_messages)}/{original_number_of_messages} messages are after most recent"
                " message."
            )

            # Fetch more messages until we get back to messages that have been bridged already.
            while len(new_messages) > 0 and len(new_messages) == original_number_of_messages:
                await asyncio.sleep(self.config["bridge.backfill.incremental.page_delay"])

                portal.log.debug("Fetching more messages for forward backfill")
                resp = await self.client.fetch_messages(
                    portal.fbid, forward_messages[0].timestamp - 1
                )
                if len(resp.nodes) == 0:
                    break
                original_number_of_messages = len(resp.nodes)
                new_messages = [m for m in resp.nodes if last_message.timestamp < m.timestamp]
                forward_messages = new_messages + forward_messages
                portal.log.debug(
                    f"{len(new_messages)}/{original_number_of_messages} messages are after most "
                    "recent message."
                )
        elif not portal.first_event_id:
            self.log.debug(
                f"Skipping backfilling {portal.fbid_log} as the first event ID is not known"
            )
            return False

        if forward_messages:
            last_message_timestamp = (
                forward_messages[0].timestamp if len(forward_messages) > 0 else None
            )
            mark_read = thread.unread_count == 0 or (
                (hours := self.config["bridge.backfill.unread_hours_threshold"]) > 0
                and last_message_timestamp
                and (
                    datetime.fromtimestamp(last_message_timestamp / 1000)
                    < datetime.now() - timedelta(hours=hours)
                )
            )
            (
                _,
                last_message_timestamp,
                base_insertion_event_id,
            ) = await portal.backfill_message_page(
                self,
                forward_messages,
                forward=True,
                last_message=last_message,
                mark_read=mark_read,
            )
            if (
                not self.bridge.homeserver_software.is_hungry
                and self.config["bridge.backfill.msc2716"]
            ):
                await portal.send_post_backfill_dummy(
                    last_message_timestamp, base_insertion_event_id=base_insertion_event_id
                )
            if (
                mark_read
                and not self.bridge.homeserver_software.is_hungry
                and (puppet := await self.get_puppet())
            ):
                last_message = await DBMessage.get_most_recent(portal.fbid, portal.fb_receiver)
                if last_message:
                    await puppet.intent_for(portal).mark_read(portal.mxid, last_message.mxid)

        if self.config["bridge.backfill.msc2716"]:
            await portal.enqueue_immediate_backfill(self, 1)
        return len(forward_messages) > 0

    async def mute_room(self, portal: po.Portal, mute_until: int | None) -> None:
        if not self.config["bridge.mute_bridging"] or not portal or not portal.mxid:
            return
        puppet = await pu.Puppet.get_by_custom_mxid(self.mxid)
        if not puppet or not puppet.is_real_user:
            return
        if mute_until is not None and (mute_until < 0 or mute_until > int(time.time())):
            await puppet.intent.set_push_rule(
                PushRuleScope.GLOBAL,
                PushRuleKind.ROOM,
                portal.mxid,
                actions=[PushActionType.DONT_NOTIFY],
            )
        else:
            try:
                await puppet.intent.remove_push_rule(
                    PushRuleScope.GLOBAL, PushRuleKind.ROOM, portal.mxid
                )
            except MNotFound:
                pass

    async def on_2fa_callback(self) -> str:
        if self.command_status and self.command_status.get("action", "") == "Login":
            future = self.loop.create_future()
            self.command_status["future"] = future
            self.command_status["next"] = enter_2fa_code
            await self.az.intent.send_notice(
                self.command_status["room_id"],
                "You have two-factor authentication enabled. Please send the code here.",
            )
            return await future
        raise RuntimeError("No ongoing login command")

    async def get_notice_room(self) -> RoomID:
        if not self.notice_room:
            async with self._notice_room_lock:
                # If someone already created the room while this call was waiting,
                # don't make a new room
                if self.notice_room:
                    return self.notice_room
                creation_content = {}
                if not self.config["bridge.federate_rooms"]:
                    creation_content["m.federate"] = False
                self.notice_room = await self.az.intent.create_room(
                    is_direct=True,
                    invitees=[self.mxid],
                    topic="Facebook Messenger bridge notices",
                    creation_content=creation_content,
                )
                await self.save()
        return self.notice_room

    async def send_bridge_notice(
        self,
        text: str,
        edit: EventID | None = None,
        state_event: BridgeStateEvent | None = None,
        important: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> EventID | None:
        if state_event:
            await self.push_bridge_state(
                state_event,
                error=error_code,
                message=error_message if error_code else text,
            )
        if self.config["bridge.disable_bridge_notices"]:
            return None
        event_id = None
        try:
            self.log.debug("Sending bridge notice: %s", text)
            content = TextMessageEventContent(
                body=text,
                msgtype=(MessageType.TEXT if important else MessageType.NOTICE),
            )
            if edit:
                content.set_edit(edit)
            # This is locked to prevent notices going out in the wrong order
            async with self._notice_send_lock:
                event_id = await self.az.intent.send_message(await self.get_notice_room(), content)
        except Exception:
            self.log.warning("Failed to send bridge notice", exc_info=True)
        return edit or event_id

    async def fill_bridge_state(self, state: BridgeState) -> None:
        await super().fill_bridge_state(state)
        if self.fbid:
            state.remote_id = str(self.fbid)
            puppet = await pu.Puppet.get_by_fbid(self.fbid)
            state.remote_name = puppet.name

    async def get_bridge_states(self) -> list[BridgeState]:
        if not self.state:
            return []
        state = BridgeState(state_event=BridgeStateEvent.UNKNOWN_ERROR)
        if self.is_connected:
            state.state_event = BridgeStateEvent.CONNECTED
        elif self._is_refreshing or self.mqtt:
            state.state_event = BridgeStateEvent.TRANSIENT_DISCONNECT
        return [state]

    async def get_puppet(self) -> pu.Puppet | None:
        if not self.fbid:
            return None
        return await pu.Puppet.get_by_fbid(self.fbid)

    async def get_portal_with(self, puppet: pu.Puppet, create: bool = True) -> po.Portal | None:
        if not self.fbid:
            return None
        return await po.Portal.get_by_fbid(
            puppet.fbid, fb_receiver=self.fbid, create=create, fb_type=ThreadType.USER
        )

    # region Facebook event handling

    def start_listen(self) -> None:
        self.listen_task = asyncio.create_task(self._try_listen())

    async def delayed_start_listen(self, sleep: int) -> None:
        await asyncio.sleep(sleep)
        if self.is_connected:
            self.log.debug(
                "Already reconnected before delay after MQTT reconnection error finished",
            )
        else:
            self.log.debug("Reconnecting after MQTT connection error")
            self.start_listen()

    def _disconnect_listener_after_error(self) -> None:
        try:
            self.mqtt.disconnect()
        except Exception:
            self.log.debug("Error disconnecting listener after error", exc_info=True)

    async def _save_seq_id_after_sleep(self) -> None:
        await asyncio.sleep(120)
        self._seq_id_save_task = None
        self.log.trace("Saving sequence ID %s", self.seq_id)
        try:
            await self.save_seq_id()
        except Exception:
            self.log.exception("Error saving sequence ID")

    def _update_seq_id(self, seq_id: int) -> None:
        self.seq_id = seq_id
        if not self._seq_id_save_task or self._seq_id_save_task.done():
            self.log.trace("Starting seq id save task (%s)", seq_id)
            self._seq_id_save_task = asyncio.create_task(self._save_seq_id_after_sleep())
        else:
            self.log.trace("Not starting seq id save task (%s)", seq_id)

    def _update_region_hint(self, region_hint: str) -> None:
        self.log.debug(f"Got region hint {region_hint}")
        if region_hint:
            self.state.session.region_hint = region_hint
            background_task.create(self.save())

    async def _try_listen(self) -> None:
        try:
            if not self.mqtt:
                self.mqtt = AndroidMQTT(
                    self.state,
                    log=self.log.getChild("mqtt"),
                    connect_token_hash=self.connect_token_hash,
                    proxy_handler=self.proxy_handler,
                )
                self.mqtt.seq_id_update_callback = self._update_seq_id
                self.mqtt.region_hint_callback = self._update_region_hint
                self.mqtt.connection_unauthorized_callback = self.on_connection_not_authorized
                self.mqtt.enable_web_presence = self.config["bridge.presence_from_facebook"]
                self.mqtt.add_event_handler(mqtt_t.Message, self.on_message)
                self.mqtt.add_event_handler(mqtt_t.ExtendedMessage, self.on_message)
                self.mqtt.add_event_handler(mqtt_t.NameChange, self.on_title_change)
                self.mqtt.add_event_handler(mqtt_t.AvatarChange, self.on_avatar_change)
                self.mqtt.add_event_handler(mqtt_t.UnsendMessage, self.on_message_unsent)
                self.mqtt.add_event_handler(
                    mqtt_t.DeltaRTCMultiwayMessage, self.on_delta_rtc_multiway_message
                )
                self.mqtt.add_event_handler(mqtt_t.ReadReceipt, self.on_message_seen)
                self.mqtt.add_event_handler(mqtt_t.OwnReadReceipt, self.on_message_seen_self)
                self.mqtt.add_event_handler(mqtt_t.Reaction, self.on_reaction)
                self.mqtt.add_event_handler(mqtt_t.Presence, self.on_presence)
                self.mqtt.add_event_handler(mqtt_t.AddMember, self.on_members_added)
                self.mqtt.add_event_handler(mqtt_t.RemoveMember, self.on_member_removed)
                self.mqtt.add_event_handler(mqtt_t.ThreadChange, self.on_thread_change)
                self.mqtt.add_event_handler(mqtt_t.MessageSyncError, self.on_message_sync_error)
                self.mqtt.add_event_handler(mqtt_t.TypingNotification, self.on_typing)
                self.mqtt.add_event_handler(mqtt_t.ForcedFetch, self.on_forced_fetch)
                self.mqtt.add_event_handler(Connect, self.on_connect)
                self.mqtt.add_event_handler(Disconnect, self.on_disconnect)
                self.mqtt.add_event_handler(ProxyUpdate, self.on_proxy_update)
            await self.mqtt.listen(self.seq_id)
            self.is_connected = False
            if not self._is_refreshing and not self.shutdown:
                await self.send_bridge_notice(
                    "Facebook Messenger connection closed without error",
                    state_event=BridgeStateEvent.UNKNOWN_ERROR,
                    error_code="fb-disconnected",
                )
        except MQTTReconnectionError as e:
            self.log.warning(
                f"Unexpected connection error: {e}, reconnecting in 1 minute",
                exc_info=True,
            )
            await self.send_bridge_notice(
                f"Error in listener: {e}",
                important=True,
                state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
                error_code="fb-connection-error",
            )
            self._disconnect_listener_after_error()
            background_task.create(self.delayed_start_listen(sleep=60))
        except (MQTTNotLoggedIn, MQTTNotConnected) as e:
            self.log.debug("Listen threw a Facebook error", exc_info=True)
            action = self.config["bridge.on_reconnection_fail.action"]
            action_name = "Not retrying!"
            if action == "reconnect":
                action_name = "Retrying..."
            elif action == "refresh":
                action_name = "Refreshing session..."
            event = (
                "Disconnected from" if isinstance(e, MQTTNotLoggedIn) else "Failed to connect to"
            )
            message = f"{event} Facebook Messenger: {e}. {action_name}"
            self.log.warning(message)
            if action not in ("reconnect", "refresh"):
                await self.send_bridge_notice(
                    message,
                    important=True,
                    state_event=BridgeStateEvent.UNKNOWN_ERROR,
                    error_code="fb-connection-error",
                )
            else:
                await self.send_bridge_notice(
                    message,
                    state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
                    error_code="fb-no-mqtt",
                )
                if self.temp_disconnect_notices:
                    await self.send_bridge_notice(message)
            if action in ("reconnect", "refresh"):
                wait_for = self.config["bridge.on_reconnection_fail.wait_for"]
                if wait_for:
                    await asyncio.sleep(get_interval(wait_for))
                # Ensure a minimum of 120s between reconnection attempts, even if wait is disabled
                sleep_time = self._prev_reconnect_fail_refresh + 120 - time.monotonic()
                if not wait_for:
                    self.log.debug(f"Waiting {sleep_time:.3f} seconds before reconnecting")
                    await asyncio.sleep(sleep_time)
                self._prev_reconnect_fail_refresh = time.monotonic()
                if action == "refresh":
                    background_task.create(self.refresh())
                else:
                    background_task.create(self.reconnect(fetch_user=True))
            else:
                self._disconnect_listener_after_error()
        except Exception:
            self.is_connected = False
            self.log.exception("Fatal error in listener")
            await self.send_bridge_notice(
                "Fatal error in listener (see logs for more info)",
                state_event=BridgeStateEvent.UNKNOWN_ERROR,
                important=True,
                error_code="fb-connection-error",
            )
            self._disconnect_listener_after_error()

    async def on_connect(self, evt: Connect) -> None:
        now = time.monotonic()
        disconnected_at = self._connection_time
        max_delay = self.config["bridge.resync_max_disconnected_time"]
        first_connect = self.is_connected is None
        self.is_connected = True
        self._track_metric(METRIC_CONNECTED, True)
        if not first_connect and disconnected_at + max_delay < now:
            duration = int(now - disconnected_at)
            self.log.debug("Disconnection lasted %d seconds, not re-syncing threads...", duration)
        elif self.temp_disconnect_notices:
            await self.send_bridge_notice("Connected to Facebook Messenger")
        await self.push_bridge_state(BridgeStateEvent.CONNECTED)

    async def on_disconnect(self, evt: Disconnect) -> None:
        self.is_connected = False
        self._track_metric(METRIC_CONNECTED, False)
        if self.temp_disconnect_notices:
            await self.send_bridge_notice(f"Disconnected from Facebook Messenger: {evt.reason}")

    async def on_proxy_update(self, evt: ProxyUpdate | None = None) -> None:
        if self.client:
            self.client.setup_http()
        if self.mqtt:
            self.mqtt.setup_proxy()
        if self.command_status:
            self.command_status["api"].setup_http()

    def stop_listen(self) -> None:
        if self.mqtt:
            self.mqtt.disconnect()
        if self.listen_task:
            self.listen_task.cancel()
        self.mqtt = None
        self.listen_task = None

    def stop_backfill_tasks(self) -> None:
        if self._backfill_loop_task:
            self._backfill_loop_task.cancel()
            self._backfill_loop_task = None
        if self._thread_sync_task:
            self._thread_sync_task.cancel()
            self._thread_sync_task = None

    async def on_logged_in(self, state: AndroidState) -> None:
        self.log.debug(f"Successfully logged in as {state.session.uid}")
        self.fbid = state.session.uid
        await self.push_bridge_state(BridgeStateEvent.CONNECTING)
        self.state = state
        self.client = AndroidAPI(
            state,
            log=self.log.getChild("api"),
            proxy_handler=self.proxy_handler,
            on_proxy_update=self.on_proxy_update,
        )
        await self.save()
        try:
            self._logged_in_info = await self.client.fetch_logged_in_user(post_login=True)
            self._logged_in_info_time = time.monotonic()
            self._is_logged_in = True
        except Exception:
            self.log.exception("Failed to fetch post-login info")
        self.stop_listen()
        self.stop_backfill_tasks()
        background_task.create(self.post_login(is_startup=True, from_login=True))

    @async_time(METRIC_MESSAGE)
    async def on_message(self, evt: mqtt_t.Message | mqtt_t.ExtendedMessage) -> None:
        if isinstance(evt, mqtt_t.ExtendedMessage):
            reply_to = evt.reply_to_message
            evt = evt.message
        else:
            reply_to = None
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        puppet = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        if not puppet.name:
            portal.schedule_resync(self, puppet)
        await portal.handle_facebook_message(self, puppet, evt, reply_to=reply_to)

    @async_time(METRIC_TITLE_CHANGE)
    async def on_title_change(self, evt: mqtt_t.NameChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.handle_facebook_name(
            self, sender, evt.new_name, evt.metadata.id, evt.metadata.timestamp
        )

    @async_time(METRIC_AVATAR_CHANGE)
    async def on_avatar_change(self, evt: mqtt_t.AvatarChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.handle_facebook_photo(
            self, sender, evt.new_avatar, evt.metadata.id, evt.metadata.timestamp
        )

    @async_time(METRIC_MESSAGE_SEEN)
    async def on_message_seen(self, evt: mqtt_t.ReadReceipt) -> None:
        puppet = await pu.Puppet.get_by_fbid(evt.user_id)
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if portal and portal.mxid:
            await portal.handle_facebook_seen(self, puppet, evt.read_to)

    @async_time(METRIC_MESSAGE_SEEN)
    async def on_message_seen_self(self, evt: mqtt_t.OwnReadReceipt) -> None:
        puppet = await pu.Puppet.get_by_fbid(self.fbid)
        for thread in evt.threads:
            portal = await po.Portal.get_by_thread(thread, self.fbid, create=False)
            if portal:
                await portal.handle_facebook_seen(self, puppet, evt.read_to)

    @async_time(METRIC_MESSAGE_UNSENT)
    async def on_message_unsent(self, evt: mqtt_t.UnsendMessage) -> None:
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if portal and portal.mxid:
            puppet = await pu.Puppet.get_by_fbid(evt.user_id)
            await portal.handle_facebook_unsend(puppet, evt.message_id, timestamp=evt.timestamp)

    peer_id_re = re.compile(r'"peer_id":"(\d+)"')
    rtc_room_id_re = re.compile(r"ROOM:(\d+)")
    rtc_room_id_to_peer_id: dict[str, int] = {}

    @async_time(METRIC_DELTA_RTC_MULTIWAY_MESSAGE)
    async def on_delta_rtc_multiway_message(self, evt: mqtt_t.DeltaRTCMultiwayMessage) -> None:
        # The data is in a really annoying format (probably flatbuffers), so we are just parsing
        # out the important bits manually.
        decoded = base64.b64decode(evt.data).decode(encoding="unicode_escape")
        rtc_room_id_match = self.rtc_room_id_re.search(decoded)
        if not rtc_room_id_match:
            return

        if evt.event != "RING":
            peer_id = self.rtc_room_id_to_peer_id.get(rtc_room_id_match.group(1))
            if not peer_id:
                return

            portal = await po.Portal.get_by_fbid(peer_id, fb_receiver=self.fbid, create=False)
            if portal and portal.mxid:
                await portal.handle_facebook_call_hangup()

            self.rtc_room_id_to_peer_id.pop(rtc_room_id_match.group(1), None)
            return

        peer_id_match = self.peer_id_re.search(decoded)
        if not peer_id_match:
            return
        peer_id = int(peer_id_match.group(1))
        self.rtc_room_id_to_peer_id[rtc_room_id_match.group(1)] = peer_id

        portal = await po.Portal.get_by_fbid(peer_id, fb_receiver=self.fbid, create=False)
        if portal and portal.mxid:
            puppet = await pu.Puppet.get_by_fbid(peer_id)
            await portal.handle_facebook_call(puppet)

    @async_time(METRIC_REACTION)
    async def on_reaction(self, evt: mqtt_t.Reaction) -> None:
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if not portal or not portal.mxid:
            return
        puppet = await pu.Puppet.get_by_fbid(evt.reaction_sender_id)
        if evt.reaction is None:
            await portal.handle_facebook_reaction_remove(self, puppet, evt.message_id)
        else:
            await portal.handle_facebook_reaction_add(self, puppet, evt.message_id, evt.reaction)

    async def on_forced_fetch(self, evt: mqtt_t.ForcedFetch) -> None:
        background_task.create(self._try_on_forced_fetch(evt))

    async def _try_on_forced_fetch(self, evt: mqtt_t.ForcedFetch) -> None:
        try:
            await self._on_forced_fetch(evt)
        except Exception:
            self.log.exception("Error in ForcedFetch handler")

    @async_time(METRIC_FORCED_FETCH)
    async def _on_forced_fetch(self, evt: mqtt_t.ForcedFetch) -> None:
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if not portal or not portal.mxid:
            return
        infos = await self.client.fetch_thread_info(portal.fbid)
        if len(infos) == 0 or infos[0].thread_key.id != portal.fbid:
            self.log.warning(f"Didn't get data when fetching thread {portal.fbid} for forced sync")
            return
        thread_info = infos[0]
        await portal.update_info(self, thread_info)
        await portal.handle_forced_fetch(self, thread_info.messages.nodes)

    @async_time(METRIC_PRESENCE)
    async def on_presence(self, evt: mqtt_t.Presence) -> None:
        for update in evt.updates:
            puppet = await pu.Puppet.get_by_fbid(update.user_id, create=False)
            if puppet:
                self.log.trace(f"Received presence for: {puppet.name} - {update.status}")
                await PresenceUpdater.set_presence(
                    puppet, PresenceState.ONLINE if update.status == 2 else PresenceState.OFFLINE
                )

    @async_time(METRIC_TYPING)
    async def on_typing(self, evt: mqtt_t.TypingNotification) -> None:
        portal = await po.Portal.get_by_fbid(evt.user_id, fb_receiver=self.fbid, create=False)
        if portal and portal.mxid:
            puppet = await pu.Puppet.get_by_fbid(evt.user_id)
            await puppet.intent.set_typing(portal.mxid, timeout=10000 if evt.typing_status else 0)

    @async_time(METRIC_MEMBERS_ADDED)
    async def on_members_added(self, evt: mqtt_t.AddMember) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        if portal.mxid:
            sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            users = [await pu.Puppet.get_by_fbid(user.id) for user in evt.users]
            await portal.handle_facebook_join(self, sender, users)

    @async_time(METRIC_MEMBER_REMOVED)
    async def on_member_removed(self, evt: mqtt_t.RemoveMember) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        if portal.mxid:
            sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            user = await pu.Puppet.get_by_fbid(evt.user_id)
            await portal.handle_facebook_leave(self, sender, user)

    @async_time(METRIC_THREAD_CHANGE)
    async def on_thread_change(self, evt: mqtt_t.ThreadChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        if not portal.mxid:
            return

        if evt.action == mqtt_t.ThreadChangeAction.NICKNAME:
            target = int(evt.action_data["participant_id"])
            puppet = await pu.Puppet.get_by_fbid(target)
            await portal.sync_per_room_nick(puppet, evt.action_data["nickname"])
        elif evt.action == mqtt_t.ThreadChangeAction.POLL:
            puppet = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            await portal.handle_facebook_poll(puppet, evt)
        elif evt.action == mqtt_t.ThreadChangeAction.CALL_LOG:
            puppet = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            await portal.handle_facebook_group_call(puppet, evt)

        # TODO
        # elif evt.action == mqtt_t.ThreadChangeAction.ADMINS:
        #     sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        #     user = await pu.Puppet.get_by_fbid(evt.action_data["TARGET_ID"])
        #     make_admin = evt.action_data["ADMIN_EVENT"] == "add_admin"
        #     # TODO does the ADMIN_TYPE data matter?
        #     await portal.handle_facebook_admin(self, sender, user, make_admin)
        else:
            self.log.trace("Unhandled thread change: %s", evt)

    async def on_message_sync_error(self, evt: mqtt_t.MessageSyncError) -> None:
        self.stop_listen()
        if evt == mqtt_t.MessageSyncError.ERROR_QUEUE_TEMPORARY_NOT_AVAILABLE:
            self.log.debug("Reconnecting in 30s after ERROR_QUEUE_TEMPORARY_NOT_AVAILABLE error")
            await asyncio.sleep(30)
            self.start_listen()
        elif evt == mqtt_t.MessageSyncError.QUEUE_NOT_FOUND:
            self.log.debug("Resetting connect_token_hash due to QUEUE_NOT_FOUND error")
            self.connect_token_hash = None
            self.start_listen()
        else:
            self.log.error(f"Message sync error: {evt.value}, resyncing...")
            await self.send_bridge_notice(f"Message sync error: {evt.value}, resyncing...")
            await self.sync_recent_threads()

    def on_connection_not_authorized(self) -> None:
        self.log.debug("Stopping listener and reloading session after MQTT not authorized error")
        self.stop_listen()
        background_task.create(self.reload_session())

    # endregion
