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
from typing import (Dict, List, Optional, AsyncIterable, Awaitable, Union, TypeVar, AsyncGenerator,
                    TYPE_CHECKING, cast)
import asyncio
import time

from mautrix.errors import MNotFound
from mautrix.types import (PushActionType, PushRuleKind, PushRuleScope, UserID, RoomID, EventID,
                           TextMessageEventContent, MessageType)
from mautrix.client import Client as MxClient
from mautrix.bridge import BaseUser, BridgeState, async_getter_lock
from mautrix.bridge._community import CommunityHelper, CommunityID
from mautrix.util.bridge_state import BridgeStateEvent
from mautrix.util.simple_lock import SimpleLock
from mautrix.util.opt_prometheus import Summary, Gauge, async_time

from maufbapi import AndroidState, AndroidMQTT, AndroidAPI
from maufbapi.mqtt import Disconnect, Connect, MQTTNotLoggedIn, MQTTNotConnected
from maufbapi.http import InvalidAccessToken, ResponseError
from maufbapi.types import graphql, mqtt as mqtt_t

from .config import Config
from .commands import enter_2fa_code
from .db import User as DBUser, UserPortal, UserContact
from . import portal as po, puppet as pu

METRIC_SYNC_THREADS = Summary('bridge_sync_threads', 'calls to sync_threads')
METRIC_RESYNC = Summary('bridge_on_resync', 'calls to on_resync')
METRIC_UNKNOWN_EVENT = Summary('bridge_on_unknown_event', 'calls to on_unknown_event')
METRIC_MEMBERS_ADDED = Summary('bridge_on_members_added', 'calls to on_members_added')
METRIC_MEMBER_REMOVED = Summary('bridge_on_member_removed', 'calls to on_member_removed')
METRIC_TYPING = Summary('bridge_on_typing', 'calls to on_typing')
METRIC_PRESENCE = Summary('bridge_on_presence', 'calls to on_presence')
METRIC_REACTION = Summary('bridge_on_reaction', 'calls to on_reaction')
METRIC_MESSAGE_UNSENT = Summary('bridge_on_unsent', 'calls to on_unsent')
METRIC_MESSAGE_SEEN = Summary('bridge_on_message_seen', 'calls to on_message_seen')
METRIC_TITLE_CHANGE = Summary('bridge_on_title_change', 'calls to on_title_change')
METRIC_AVATAR_CHANGE = Summary('bridge_on_avatar_change', 'calls to on_avatar_change')
METRIC_THREAD_CHANGE = Summary('bridge_on_thread_change', 'calls to on_thread_change')
METRIC_MESSAGE = Summary('bridge_on_message', 'calls to on_message')
METRIC_LOGGED_IN = Gauge('bridge_logged_in', 'Users logged into the bridge')
METRIC_CONNECTED = Gauge('bridge_connected', 'Bridge users connected to Facebook')

if TYPE_CHECKING:
    from .__main__ import MessengerBridge

try:
    from aiohttp_socks import ProxyError, ProxyConnectionError, ProxyTimeoutError
except ImportError:
    class ProxyError(Exception):
        pass


    ProxyConnectionError = ProxyTimeoutError = ProxyError

T = TypeVar('T')

BridgeState.human_readable_errors.update({
    "fb-reconnection-error": "Failed to reconnect to Messenger",
    "fb-connection-error": "Messenger disconnected unexpectedly",
    "fb-auth-error": "Authentication error from Messenger: {message}",
    "fb-disconnected": None,
    "fb-no-mqtt": "You're not connected to Messenger",
    "logged-out": "You're not logged into Messenger",
})


class User(DBUser, BaseUser):
    temp_disconnect_notices: bool = True
    shutdown: bool = False
    config: Config

    by_mxid: Dict[UserID, 'User'] = {}
    by_fbid: Dict[int, 'User'] = {}

    client: Optional[AndroidAPI]
    mqtt: Optional[AndroidMQTT]
    listen_task: Optional[asyncio.Task]
    seq_id: Optional[int]

    _notice_room_lock: asyncio.Lock
    _notice_send_lock: asyncio.Lock
    is_admin: bool
    permission_level: str
    _is_logged_in: Optional[bool]
    _is_connected: Optional[bool]
    _connection_time: float
    _prev_thread_sync: float
    _prev_reconnect_fail_refresh: float
    _db_instance: Optional[DBUser]
    _sync_lock: SimpleLock
    _is_refreshing: bool

    _community_helper: CommunityHelper
    _community_id: Optional[CommunityID]

    def __init__(self, mxid: UserID, fbid: Optional[int] = None,
                 state: Optional[AndroidState] = None,
                 notice_room: Optional[RoomID] = None) -> None:
        super().__init__(mxid=mxid, fbid=fbid, state=state, notice_room=notice_room)
        BaseUser.__init__(self)
        self.notice_room = notice_room
        self._notice_room_lock = asyncio.Lock()
        self._notice_send_lock = asyncio.Lock()
        self.command_status = None
        (self.is_whitelisted, self.is_admin,
         self.permission_level) = self.config.get_permissions(mxid)
        self._is_logged_in = None
        self._is_connected = None
        self._connection_time = time.monotonic()
        self._prev_thread_sync = -10
        self._prev_reconnect_fail_refresh = time.monotonic()
        self._community_id = None
        self._sync_lock = SimpleLock("Waiting for thread sync to finish before handling %s",
                                     log=self.log)
        self._is_refreshing = False

        self.log = self.log.getChild(self.mxid)

        self.client = None
        self.mqtt = None
        self.listen_task = None
        self.seq_id = None

    @classmethod
    def init_cls(cls, bridge: 'MessengerBridge') -> AsyncIterable[Awaitable[bool]]:
        cls.bridge = bridge
        cls.config = bridge.config
        cls.az = bridge.az
        cls.loop = bridge.loop
        cls._community_helper = CommunityHelper(cls.az)
        cls.temp_disconnect_notices = bridge.config["bridge.temporary_disconnect_notices"]
        return (user.load_session() async for user in cls.all_logged_in())

    @property
    def is_connected(self) -> Optional[bool]:
        return self._is_connected

    @is_connected.setter
    def is_connected(self, val: Optional[bool]) -> None:
        if self._is_connected != val:
            self._is_connected = val
            self._connection_time = time.monotonic()

    # region Database getters

    def _add_to_cache(self) -> None:
        self.by_mxid[self.mxid] = self
        if self.fbid:
            self.by_fbid[self.fbid] = self

    @classmethod
    async def all_logged_in(cls) -> AsyncGenerator['User', None]:
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
    async def get_by_mxid(cls, mxid: UserID, *, create: bool = True) -> Optional['User']:
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
    async def get_by_fbid(cls, fbid: int) -> Optional['User']:
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

    async def load_session(self, _override: bool = False, _raise_errors: bool = False) -> bool:
        if self._is_logged_in and not _override:
            return True
        elif not self.state:
            return False
        attempt = 0
        client = AndroidAPI(self.state, log=self.log.getChild("api"))
        while True:
            try:
                user_info = await client.get_self()
                break
            except (ProxyError, ProxyTimeoutError, ProxyConnectionError, ConnectionError) as e:
                attempt += 1
                wait = min(attempt * 10, 60)
                self.log.warning(f"{e.__class__.__name__} while trying to restore session, "
                                 f"retrying in {wait} seconds: {e}")
                await asyncio.sleep(wait)
            except Exception:
                self.log.exception("Failed to restore session")
                if _raise_errors:
                    raise
                return False
        if user_info:
            self.log.info("Loaded session successfully")
            self.client = client
            self._track_metric(METRIC_LOGGED_IN, True)
            self._is_logged_in = True
            self.is_connected = None
            self.stop_listen()
            asyncio.create_task(self.post_login())
            return True
        return False

    async def is_logged_in(self, _override: bool = False) -> bool:
        if not self.state or not self.client:
            return False
        if self._is_logged_in is None or _override:
            try:
                self._is_logged_in = bool(await self.client.get_self())
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
        await self._reload_session(event_id)

    async def _reload_session(self, event_id: EventID, retries: int = 3) -> None:
        try:
            await self.load_session(_override=True, _raise_errors=True)
        except InvalidAccessToken as e:
            await self.send_bridge_notice(
                "Got authentication error from Messenger:\n\n"
                f"> {e!s}\n\n"
                "If you changed your Facebook password or enabled two-factor authentication, this "
                "is normal and you just need to log in again.",
                edit=event_id,
                important=True,
                state_event=BridgeStateEvent.BAD_CREDENTIALS,
                error_code="fb-auth-error",
                error_message=str(e)
            )
            await self.logout(remove_fbid=False)
        except ResponseError as e:
            will_retry = retries > 0
            retry = "Retrying in 1 minute" if will_retry else "Not retrying"
            notice = f"Failed to refresh Messenger session: unknown response error {e}. {retry}"
            if will_retry:
                await self.send_bridge_notice(notice, edit=event_id,
                                              state_event=BridgeStateEvent.TRANSIENT_DISCONNECT)
                await asyncio.sleep(60)
                await self._reload_session(event_id, retries - 1)
            else:
                await self.send_bridge_notice(notice, edit=event_id, important=True,
                                              state_event=BridgeStateEvent.UNKNOWN_ERROR,
                                              error_code="fb-reconnection-error")
        except Exception:
            await self.send_bridge_notice("Failed to refresh Messenger session: unknown error "
                                          "(see logs for more details)", edit=event_id,
                                          state_event=BridgeStateEvent.UNKNOWN_ERROR,
                                          error_code="fb-reconnection-error")
        finally:
            self._is_refreshing = False

    async def reconnect(self) -> None:
        self._is_refreshing = True
        if self.mqtt:
            self.mqtt.disconnect()
        await self.listen_task
        self.listen_task = None
        self.mqtt = None
        self.start_listen()
        self._is_refreshing = False

    async def logout(self, remove_fbid: bool = True) -> bool:
        ok = True
        self.stop_listen()
        if self.state:
            # TODO is there even a logout API for messenger mobile?
            pass
            # try:
            #     await self.session.logout()
            # except fbchat.FacebookError:
            #     self.log.exception("Error while logging out")
            #     ok = False
        if remove_fbid:
            await self.push_bridge_state(BridgeStateEvent.LOGGED_OUT)
        self._track_metric(METRIC_LOGGED_IN, False)
        self.state = None
        self._is_logged_in = False
        self.is_connected = None
        self.client = None
        self.mqtt = None

        if self.fbid and remove_fbid:
            await UserContact.delete_all(self.fbid)
            await UserPortal.delete_all(self.fbid)
            del self.by_fbid[self.fbid]
            self.fbid = None

        await self.save()
        return ok

    async def post_login(self) -> None:
        self.log.info("Running post-login actions")
        self._add_to_cache()

        try:
            puppet = await pu.Puppet.get_by_fbid(self.fbid)

            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info(f"Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        await self._create_community()
        await self.sync_threads()
        self.start_listen()

    async def _create_community(self) -> None:
        template = self.config["bridge.community_template"]
        if not template:
            return
        localpart, server = MxClient.parse_user_id(self.mxid)
        community_localpart = template.format(localpart=localpart, server=server)
        self.log.debug(f"Creating personal filtering community {community_localpart}...")
        self._community_id, created = await self._community_helper.create(community_localpart)
        if created:
            await self._community_helper.update(self._community_id, name="Facebook Messenger",
                                                avatar_url=self.config["appservice.bot_avatar"],
                                                short_desc="Your Facebook bridged chats")
            await self._community_helper.invite(self._community_id, self.mxid)

    async def _add_community(self, up: Optional[UserPortal], contact: Optional[UserContact],
                             portal: 'po.Portal', puppet: Optional['pu.Puppet']) -> None:
        if portal.mxid:
            if not up or not up.in_community:
                ic = await self._community_helper.add_room(self._community_id, portal.mxid)
                if up and ic:
                    up.in_community = True
                    await up.save()
                elif not up:
                    await UserPortal(user=self.fbid, in_community=ic, portal=portal.fbid,
                                     portal_receiver=portal.fb_receiver).insert()
        if puppet:
            await self._add_community_puppet(contact, puppet)

    async def _add_community_puppet(self, contact: Optional[UserContact],
                                    puppet: 'pu.Puppet') -> None:
        if not contact or not contact.in_community:
            await puppet.default_mxid_intent.ensure_registered()
            ic = await self._community_helper.join(self._community_id,
                                                   puppet.default_mxid_intent)
            if contact and ic:
                contact.in_community = True
                await contact.save()
            elif not contact:
                # This uses upsert instead of insert as a hacky fix for potential conflicts
                await UserContact(user=self.fbid, contact=puppet.fbid, in_community=ic).upsert()

    async def get_direct_chats(self) -> Dict[UserID, List[RoomID]]:
        return {
            pu.Puppet.get_mxid_from_id(portal.fbid): [portal.mxid]
            async for portal in po.Portal.get_all_by_receiver(self.fbid)
            if portal.mxid
        }

    @async_time(METRIC_SYNC_THREADS)
    async def sync_threads(self) -> None:
        if self._prev_thread_sync + 10 > time.monotonic() and self.mqtt.seq_id is not None:
            self.log.debug("Previous thread sync was less than 10 seconds ago, not re-syncing")
            return
        self._prev_thread_sync = time.monotonic()
        try:
            await self._sync_threads()
        except Exception:
            self.log.exception("Failed to sync threads")

    async def _sync_threads(self) -> None:
        sync_count = self.config["bridge.initial_chat_sync"]
        self.log.debug("Fetching threads...")
        ups = await UserPortal.all(self.fbid)
        contacts = await UserContact.all(self.fbid)
        # TODO paginate with 20 threads per request
        resp = await self.client.fetch_thread_list(thread_count=sync_count)
        self.seq_id = int(resp.sync_sequence_id)
        if self.mqtt:
            self.mqtt.seq_id = self.seq_id
        if sync_count <= 0:
            return
        await self.push_bridge_state(BridgeStateEvent.BACKFILLING)
        for thread in resp.nodes:
            try:
                await self._sync_thread(thread, ups, contacts)
            except Exception:
                self.log.exception("Failed to sync thread %s", thread.id)

        await self.update_direct_chats()

    async def _sync_thread(self, thread: graphql.Thread, ups: Dict[int, UserPortal],
                           contacts: Dict[int, UserContact]) -> None:
        self.log.debug(f"Syncing thread {thread.thread_key.id}")
        is_direct = bool(thread.thread_key.other_user_id)
        portal = await po.Portal.get_by_thread(thread.thread_key, self.fbid)
        puppet = (await pu.Puppet.get_by_fbid(thread.thread_key.other_user_id)
                  if is_direct else None)

        await self._add_community(ups.get(portal.fbid, None),
                                  contacts.get(puppet.fbid, None) if puppet else None,
                                  portal, puppet)

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
            await puppet.intent.set_push_rule(PushRuleScope.GLOBAL, PushRuleKind.ROOM, portal.mxid,
                                              actions=[PushActionType.DONT_NOTIFY])
        else:
            try:
                await puppet.intent.remove_push_rule(PushRuleScope.GLOBAL, PushRuleKind.ROOM,
                                                     portal.mxid)
            except MNotFound:
                pass

    async def is_in_portal(self, portal: 'po.Portal') -> bool:
        return await UserPortal.get(self.fbid, portal.fbid, portal.fb_receiver) is not None

    async def on_2fa_callback(self) -> str:
        if self.command_status and self.command_status.get("action", "") == "Login":
            future = self.loop.create_future()
            self.command_status["future"] = future
            self.command_status["next"] = enter_2fa_code
            await self.az.intent.send_notice(self.command_status["room_id"],
                                             "You have two-factor authentication enabled. "
                                             "Please send the code here.")
            return await future
        raise RuntimeError("No ongoing login command")

    async def get_notice_room(self) -> RoomID:
        if not self.notice_room:
            async with self._notice_room_lock:
                # If someone already created the room while this call was waiting,
                # don't make a new room
                if self.notice_room:
                    return self.notice_room
                self.notice_room = await self.az.intent.create_room(
                    is_direct=True, invitees=[self.mxid],
                    topic="Facebook Messenger bridge notices")
                await self.save()
        return self.notice_room

    async def send_bridge_notice(self, text: str, edit: Optional[EventID] = None,
                                 state_event: Optional[BridgeStateEvent] = None,
                                 important: bool = False, error_code: Optional[str] = None,
                                 error_message: Optional[str] = None) -> Optional[EventID]:
        if state_event:
            await self.push_bridge_state(state_event, error=error_code,
                                         message=error_message if error_code else text)
        if self.config["bridge.disable_bridge_notices"]:
            return None
        event_id = None
        try:
            self.log.debug("Sending bridge notice: %s", text)
            content = TextMessageEventContent(body=text, msgtype=(MessageType.TEXT if important
                                                                  else MessageType.NOTICE))
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
        state.remote_id = str(self.fbid)
        puppet = await pu.Puppet.get_by_fbid(self.fbid)
        state.remote_name = puppet.name

    # region Facebook event handling

    def start_listen(self) -> None:
        self.listen_task = asyncio.create_task(self._try_listen())

    def _disconnect_listener_after_error(self) -> None:
        try:
            self.mqtt.disconnect()
        except Exception:
            self.log.debug("Error disconnecting listener after error", exc_info=True)

    def _update_seq_id(self, seq_id: int) -> None:
        self.seq_id = seq_id

    def _update_region_hint(self, region_hint: str) -> None:
        self.log.debug(f"Got region hint {region_hint}")
        self.state.session.region_hint = region_hint
        asyncio.create_task(self.save())

    async def _try_listen(self) -> None:
        try:
            if not self.mqtt:
                self.mqtt = AndroidMQTT(self.state, log=self.log.getChild("mqtt"))
                self.mqtt.seq_id_update_callback = self._update_seq_id
                self.mqtt.region_hint_callback = self._update_region_hint
                self.mqtt.add_event_handler(mqtt_t.Message, self.on_message)
                self.mqtt.add_event_handler(mqtt_t.ExtendedMessage, self.on_message)
                self.mqtt.add_event_handler(mqtt_t.NameChange, self.on_title_change)
                self.mqtt.add_event_handler(mqtt_t.AvatarChange, self.on_avatar_change)
                self.mqtt.add_event_handler(mqtt_t.UnsendMessage, self.on_message_unsent)
                self.mqtt.add_event_handler(mqtt_t.ReadReceipt, self.on_message_seen)
                self.mqtt.add_event_handler(mqtt_t.OwnReadReceipt, self.on_message_seen_self)
                self.mqtt.add_event_handler(mqtt_t.Reaction, self.on_reaction)
                self.mqtt.add_event_handler(mqtt_t.AddMember, self.on_members_added)
                self.mqtt.add_event_handler(mqtt_t.RemoveMember, self.on_member_removed)
                self.mqtt.add_event_handler(mqtt_t.ThreadChange, self.on_thread_change)
                self.mqtt.add_event_handler(mqtt_t.MessageSyncError, self.on_message_sync_error)
                self.mqtt.add_event_handler(Connect, self.on_connect)
                self.mqtt.add_event_handler(Disconnect, self.on_disconnect)
            await self.mqtt.listen(self.seq_id)
            self.is_connected = False
            if not self._is_refreshing and not self.shutdown:
                await self.send_bridge_notice("Facebook Messenger connection closed without error",
                                              state_event=BridgeStateEvent.UNKNOWN_ERROR,
                                              error_code="fb-disconnected")
        except (MQTTNotLoggedIn, MQTTNotConnected) as e:
            self.log.debug("Listen threw a Facebook error", exc_info=True)
            refresh = (self.config["bridge.refresh_on_reconnection_fail"]
                       and self._prev_reconnect_fail_refresh + 120 < time.monotonic())
            next_action = ("Refreshing session..." if refresh else "Not retrying!")
            event = ("Disconnected from" if isinstance(e, MQTTNotLoggedIn)
                     else "Failed to connect to")
            message = f"{event} Facebook Messenger: {e}. {next_action}"
            self.log.warning(message)
            if not refresh:
                await self.send_bridge_notice(message, important=True,
                                              state_event=BridgeStateEvent.UNKNOWN_ERROR,
                                              error_code="fb-connection-error")
            elif self.temp_disconnect_notices:
                await self.send_bridge_notice(message)
            if refresh:
                self._prev_reconnect_fail_refresh = time.monotonic()
                asyncio.create_task(self.refresh())
            else:
                self._disconnect_listener_after_error()
        except Exception:
            self.is_connected = False
            self.log.exception("Fatal error in listener")
            await self.send_bridge_notice("Fatal error in listener (see logs for more info)",
                                          state_event=BridgeStateEvent.UNKNOWN_ERROR,
                                          important=True, error_code="fb-connection-error")
            self._disconnect_listener_after_error()

    # @async_time(METRIC_UNKNOWN_EVENT)
    # async def on_unknown_event(self, evt: fbchat.UnknownEvent) -> None:
    #     self.log.debug(f"Unknown event %s: %s", evt.source, evt.data)

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

    # @async_time(METRIC_RESYNC)
    # async def on_resync(self, evt: fbchat.Resync) -> None:
    #     self.log.info("sequence_id changed, resyncing threads...")
    #     await self.sync_threads()

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
        self.state = state
        self.client = AndroidAPI(state, log=self.log.getChild("api"))
        await self.save()
        self.stop_listen()
        asyncio.create_task(self.post_login())

    @async_time(METRIC_MESSAGE)
    async def on_message(self, evt: Union[mqtt_t.Message, mqtt_t.ExtendedMessage]) -> None:
        if isinstance(evt, mqtt_t.ExtendedMessage):
            reply_to = evt.reply_to_message
            evt = evt.message
        else:
            reply_to = None
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        puppet = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        # if not puppet.name:
        #     await puppet.update_info(self)
        await portal.backfill_lock.wait(evt.metadata.id)
        await portal.handle_facebook_message(self, puppet, evt, reply_to=reply_to)

    @async_time(METRIC_TITLE_CHANGE)
    async def on_title_change(self, evt: mqtt_t.NameChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.backfill_lock.wait("title change")
        await portal.handle_facebook_name(self, sender, evt.new_name, evt.metadata.id,
                                          evt.metadata.timestamp)

    @async_time(METRIC_AVATAR_CHANGE)
    async def on_avatar_change(self, evt: mqtt_t.AvatarChange) -> None:
        portal = await po.Portal.get_by_thread(evt.metadata.thread, self.fbid)
        sender = await pu.Puppet.get_by_fbid(evt.metadata.sender)
        await portal.backfill_lock.wait("avatar change")
        await portal.handle_facebook_photo(self, sender, evt.new_avatar, evt.metadata.id,
                                           evt.metadata.timestamp)

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

    # @async_time(METRIC_PRESENCE)
    # async def on_presence(self, evt: fbchat.Presence) -> None:
    #     for user, status in evt.statuses.items():
    #         puppet = pu.Puppet.get_by_fbid(user, create=False)
    #         if puppet:
    #             await puppet.default_mxid_intent.set_presence(
    #                 presence=PresenceState.ONLINE if status.active else PresenceState.OFFLINE,
    #                 ignore_cache=True)
    #
    # @async_time(METRIC_TYPING)
    # async def on_typing(self, evt: fbchat.Typing) -> None:
    #     fb_receiver = self.fbid if isinstance(evt.thread, fbchat.User) else None
    #     portal = po.Portal.get_by_thread(evt.thread, fb_receiver)
    #     if portal.mxid and not portal.backfill_lock.locked:
    #         puppet = pu.Puppet.get_by_fbid(evt.author.id)
    #         await puppet.intent.set_typing(portal.mxid, is_typing=evt.status, timeout=120000)

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
        self.log.error(f"Message sync error: {evt.value}, resyncing...")
        await self.send_bridge_notice(f"Message sync error: {evt.value}, resyncing...")
        self.stop_listen()
        await self.sync_threads()
        self.start_listen()

    # endregion
