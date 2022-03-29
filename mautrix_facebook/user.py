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

from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterable, Awaitable, TypeVar, cast
import asyncio
import hashlib
import hmac
import time

from aiohttp import ClientConnectionError

from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.http import InvalidAccessToken, ResponseError
from maufbapi.mqtt import Connect, Disconnect, MQTTNotConnected, MQTTNotLoggedIn
from maufbapi.types import graphql, mqtt as mqtt_t
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
from mautrix.util.bridge_state import BridgeState, BridgeStateEvent
from mautrix.util.opt_prometheus import Gauge, Summary, async_time
from mautrix.util.simple_lock import SimpleLock

from . import portal as po, puppet as pu
from .commands import enter_2fa_code
from .config import Config
from .db import Message as DBMessage, ThreadType, User as DBUser, UserPortal
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
    ) -> None:
        super().__init__(
            mxid=mxid,
            fbid=fbid,
            state=state,
            notice_room=notice_room,
            seq_id=seq_id,
            connect_token_hash=connect_token_hash,
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
            await self.push_bridge_state(
                BridgeStateEvent.BAD_CREDENTIALS,
                error="logged-out",
            )
            return False
        self.state.device.connection_type = self.config["facebook.connection_type"]
        self.state.carrier.name = self.config["facebook.carrier"]
        self.state.carrier.hni = self.config["facebook.hni"]
        client = AndroidAPI(self.state, log=self.log.getChild("api"))
        user_info = await self.fetch_logged_in_user(client)
        if user_info:
            self.log.info("Loaded session successfully")
            self.client = client
            self._logged_in_info = user_info
            self._logged_in_info_time = time.monotonic()
            self._track_metric(METRIC_LOGGED_IN, True)
            self._is_logged_in = True
            self.is_connected = None
            self.stop_listen()
            asyncio.create_task(self.post_login(is_startup=is_startup))
            return True
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
        self, client: AndroidAPI | None = None, action: str = "restore session"
    ) -> None:
        if not client:
            client = self.client
        attempt = 0
        while True:
            try:
                return await client.fetch_logged_in_user()
            except InvalidAccessToken as e:
                if action != "restore session":
                    await self._send_reset_notice(e)
                raise
            except (
                ProxyError,
                ProxyTimeoutError,
                ProxyConnectionError,
                ClientConnectionError,
                ConnectionError,
                asyncio.TimeoutError,
            ) as e:
                attempt += 1
                wait = min(attempt * 10, 60)
                self.log.warning(
                    f"{e.__class__.__name__} while trying to {action}, "
                    f"retrying in {wait} seconds: {e}"
                )
                await asyncio.sleep(wait)
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
            if self.client:
                self.client.sequence_id_callback = None
        if self.temp_disconnect_notices or force_notice:
            event_id = await self.send_bridge_notice(
                "Refreshing session...",
                edit=event_id,
                state_event=BridgeStateEvent.TRANSIENT_DISCONNECT,
            )
        await self.reload_session(event_id)

    async def reload_session(
        self, event_id: EventID | None = None, retries: int = 3, is_startup: bool = False
    ) -> None:
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

    async def reconnect(self, fetch_user: bool = False) -> None:
        self._is_refreshing = True
        if self.mqtt:
            self.mqtt.disconnect()
        await self.listen_task
        self.listen_task = None
        self.mqtt = None
        if fetch_user:
            self.log.debug("Fetching current user after MQTT disconnection")
            await self.fetch_logged_in_user(action="fetch current user after MQTT disconnection")
        self.start_listen()
        self._is_refreshing = False

    async def logout(self, remove_fbid: bool = True, from_auth_error: bool = False) -> bool:
        ok = True
        self.stop_listen()
        if self.state and self.client and not from_auth_error:
            try:
                ok = await self.client.logout()
            except Exception:
                self.log.warning("Error while sending logout request", exc_info=True)
        if remove_fbid:
            await self.push_bridge_state(BridgeStateEvent.LOGGED_OUT)
        self._track_metric(METRIC_LOGGED_IN, False)
        self.state = None
        self._is_logged_in = False
        self.is_connected = None
        self.client = None
        self.mqtt = None
        self.seq_id = None
        self.connect_token_hash = None

        if self.fbid and remove_fbid:
            await UserPortal.delete_all(self.fbid)
            del self.by_fbid[self.fbid]
            self.fbid = None

        await self.save()
        return ok

    async def post_login(self, is_startup: bool) -> None:
        self.log.info("Running post-login actions")
        self._add_to_cache()

        try:
            puppet = await pu.Puppet.get_by_fbid(self.fbid)

            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info(f"Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        if self.config["bridge.sync_on_startup"] or not is_startup or not self.seq_id:
            await self.sync_threads(start_listen=True)
        else:
            self.start_listen()

    async def get_direct_chats(self) -> dict[UserID, list[RoomID]]:
        return {
            pu.Puppet.get_mxid_from_id(portal.fbid): [portal.mxid]
            async for portal in po.Portal.get_all_by_receiver(self.fbid)
            if portal.mxid
        }

    @async_time(METRIC_SYNC_THREADS)
    async def sync_threads(self, start_listen: bool = False) -> bool:
        if (
            self._prev_thread_sync + 10 > time.monotonic()
            and self.mqtt
            and self.mqtt.seq_id is not None
        ):
            self.log.debug("Previous thread sync was less than 10 seconds ago, not re-syncing")
            if start_listen:
                self.start_listen()
            return True
        self._prev_thread_sync = time.monotonic()
        try:
            await self._sync_threads(start_listen=start_listen)
            return True
        except InvalidAccessToken as e:
            await self.send_bridge_notice(
                f"Got authentication error from Messenger:\n\n> {e!s}\n\n",
                important=True,
                state_event=BridgeStateEvent.BAD_CREDENTIALS,
                error_code="fb-auth-error",
                error_message=str(e),
            )
            await self.logout(remove_fbid=False, from_auth_error=True)
        except Exception as e:
            self.log.exception("Failed to sync threads")
            await self.push_bridge_state(BridgeStateEvent.UNKNOWN_ERROR, message=str(e))
        return False

    async def _sync_threads(self, start_listen: bool) -> None:
        sync_count = self.config["bridge.initial_chat_sync"]
        self.log.debug("Fetching threads...")
        # TODO paginate with 20 threads per request
        resp = await self.client.fetch_thread_list(thread_count=sync_count)
        self.seq_id = int(resp.sync_sequence_id)
        if self.mqtt:
            self.mqtt.seq_id = self.seq_id
        await self.save_seq_id()
        if start_listen:
            self.start_listen()
        if sync_count <= 0:
            return
        await self.push_bridge_state(BridgeStateEvent.BACKFILLING)
        for thread in resp.nodes:
            try:
                await self._sync_thread(thread)
            except Exception:
                self.log.exception("Failed to sync thread %s", thread.id)

        await self.update_direct_chats()

    async def _sync_thread(self, thread: graphql.Thread) -> None:
        self.log.debug(f"Syncing thread {thread.thread_key.id}")
        portal = await po.Portal.get_by_thread(thread.thread_key, self.fbid)

        was_created = False
        if not portal.mxid:
            await portal.create_matrix_room(self, thread)
            was_created = True
        else:
            await portal.update_matrix_room(self, thread)
            await portal.backfill(self, is_initial=False, thread=thread)
        if was_created or not self.config["bridge.tag_only_on_create"]:
            await self._mute_room(portal, thread.mute_until)

    async def _mute_room(self, portal: po.Portal, mute_until: int) -> None:
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

    async def is_in_portal(self, portal: po.Portal) -> bool:
        return await UserPortal.get(self.fbid, portal.fbid, portal.fb_receiver) is not None

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
            asyncio.create_task(self.save())

    async def _try_listen(self) -> None:
        try:
            if not self.mqtt:
                self.mqtt = AndroidMQTT(
                    self.state,
                    log=self.log.getChild("mqtt"),
                    connect_token_hash=self.connect_token_hash,
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
            await self.mqtt.listen(self.seq_id)
            self.is_connected = False
            if not self._is_refreshing and not self.shutdown:
                await self.send_bridge_notice(
                    "Facebook Messenger connection closed without error",
                    state_event=BridgeStateEvent.UNKNOWN_ERROR,
                    error_code="fb-disconnected",
                )
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
            elif self.temp_disconnect_notices:
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
                    asyncio.create_task(self.refresh())
                else:
                    asyncio.create_task(self.reconnect(fetch_user=True))
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
        await self.push_bridge_state(BridgeStateEvent.TRANSIENT_DISCONNECT, message=evt.reason)

    def stop_listen(self) -> None:
        if self.mqtt:
            self.mqtt.disconnect()
        if self.listen_task:
            self.listen_task.cancel()
        self.mqtt = None
        self.listen_task = None

    async def on_logged_in(self, state: AndroidState) -> None:
        self.log.debug(f"Successfully logged in as {state.session.uid}")
        self.fbid = state.session.uid
        await self.push_bridge_state(BridgeStateEvent.CONNECTING)
        self.state = state
        self.client = AndroidAPI(state, log=self.log.getChild("api"))
        await self.save()
        try:
            self._logged_in_info = await self.client.fetch_logged_in_user(post_login=True)
            self._logged_in_info_time = time.monotonic()
        except Exception:
            self.log.exception("Failed to fetch post-login info")
        self.stop_listen()
        asyncio.create_task(self.post_login(is_startup=True))

    @async_time(METRIC_MESSAGE)
    async def on_message(self, evt: mqtt_t.Message | mqtt_t.ExtendedMessage) -> None:
        if isinstance(evt, mqtt_t.ExtendedMessage):
            reply_to = evt.reply_to_message
            evt = evt.message
        else:
            reply_to = None
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        puppet = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.backfill_lock.wait(evt.metadata.id)
        if not puppet.name:
            portal.schedule_resync(self, puppet)
        await portal.handle_facebook_message(self, puppet, evt, reply_to=reply_to)

    @async_time(METRIC_TITLE_CHANGE)
    async def on_title_change(self, evt: mqtt_t.NameChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.backfill_lock.wait("title change")
        await portal.handle_facebook_name(
            self, sender, evt.new_name, evt.metadata.id, evt.metadata.timestamp
        )

    @async_time(METRIC_AVATAR_CHANGE)
    async def on_avatar_change(self, evt: mqtt_t.AvatarChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.backfill_lock.wait("avatar change")
        await portal.handle_facebook_photo(
            self, sender, evt.new_avatar, evt.metadata.id, evt.metadata.timestamp
        )

    @async_time(METRIC_MESSAGE_SEEN)
    async def on_message_seen(self, evt: mqtt_t.ReadReceipt) -> None:
        puppet = await pu.Puppet.get_by_fbid(evt.user_id)
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if portal and portal.mxid:
            await portal.backfill_lock.wait(f"read receipt from {puppet.fbid}")
            await portal.handle_facebook_seen(self, puppet, evt.read_to)

    @async_time(METRIC_MESSAGE_SEEN)
    async def on_message_seen_self(self, evt: mqtt_t.OwnReadReceipt) -> None:
        puppet = await pu.Puppet.get_by_fbid(self.fbid)
        for thread in evt.threads:
            portal = await po.Portal.get_by_thread(thread, self.fbid, create=False)
            if portal:
                await portal.backfill_lock.wait(f"read receipt from {puppet.fbid}")
                await portal.handle_facebook_seen(self, puppet, evt.read_to)

    @async_time(METRIC_MESSAGE_UNSENT)
    async def on_message_unsent(self, evt: mqtt_t.UnsendMessage) -> None:
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if portal and portal.mxid:
            await portal.backfill_lock.wait(f"redaction of {evt.message_id}")
            puppet = await pu.Puppet.get_by_fbid(evt.user_id)
            await portal.handle_facebook_unsend(puppet, evt.message_id, timestamp=evt.timestamp)

    @async_time(METRIC_REACTION)
    async def on_reaction(self, evt: mqtt_t.Reaction) -> None:
        portal = await po.Portal.get_by_thread(evt.thread, self.fbid, create=False)
        if not portal or not portal.mxid:
            return
        puppet = await pu.Puppet.get_by_fbid(evt.reaction_sender_id)
        await portal.backfill_lock.wait(f"reaction to {evt.message_id}")
        if evt.reaction is None:
            await portal.handle_facebook_reaction_remove(self, puppet, evt.message_id)
        else:
            await portal.handle_facebook_reaction_add(self, puppet, evt.message_id, evt.reaction)

    async def on_forced_fetch(self, evt: mqtt_t.ForcedFetch) -> None:
        asyncio.create_task(self._try_on_forced_fetch(evt))

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
        if portal and portal.mxid and not portal.backfill_lock.locked:
            puppet = await pu.Puppet.get_by_fbid(evt.user_id)
            await puppet.intent.set_typing(
                portal.mxid, is_typing=bool(evt.typing_status), timeout=10000
            )

    @async_time(METRIC_MEMBERS_ADDED)
    async def on_members_added(self, evt: mqtt_t.AddMember) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        if portal.mxid:
            sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            users = [await pu.Puppet.get_by_fbid(user.id) for user in evt.users]
            await portal.backfill_lock.wait("member add")
            await portal.handle_facebook_join(self, sender, users)

    @async_time(METRIC_MEMBER_REMOVED)
    async def on_member_removed(self, evt: mqtt_t.RemoveMember) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        if portal.mxid:
            sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
            user = await pu.Puppet.get_by_fbid(evt.user_id)
            await portal.backfill_lock.wait("member remove")
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

        # TODO
        # elif evt.action == mqtt_t.ThreadChangeAction.ADMINS:
        #     sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        #     user = await pu.Puppet.get_by_fbid(evt.action_data["TARGET_ID"])
        #     make_admin = evt.action_data["ADMIN_EVENT"] == "add_admin"
        #     # TODO does the ADMIN_TYPE data matter?
        #     await portal.backfill_lock.wait("admin change")
        #     await portal.handle_facebook_admin(self, sender, user, make_admin)
        else:
            self.log.trace("Unhandled thread change: %s", evt)

    async def on_message_sync_error(self, evt: mqtt_t.MessageSyncError) -> None:
        self.stop_listen()
        if evt == mqtt_t.MessageSyncError.QUEUE_NOT_FOUND:
            self.log.debug("Resetting connect_token_hash due to QUEUE_NOT_FOUND error")
            self.connect_token_hash = None
            self.start_listen()
        else:
            self.log.error(f"Message sync error: {evt.value}, resyncing...")
            await self.send_bridge_notice(f"Message sync error: {evt.value}, resyncing...")
            await self.sync_threads(start_listen=True)

    def on_connection_not_authorized(self) -> None:
        self.log.debug("Stopping listener and reloading session after MQTT not authorized error")
        self.stop_listen()
        asyncio.create_task(self.reload_session())

    # endregion
