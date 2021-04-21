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
from typing import (Dict, Deque, Optional, Tuple, Union, Set, AsyncGenerator, List, Any, Awaitable,
                    Pattern, TYPE_CHECKING, cast)
from collections import deque
from html import escape
from io import BytesIO
import mimetypes
import asyncio
import time
import re

from yarl import URL
import magic

from mautrix.types import (RoomID, EventType, ContentURI, MessageEventContent, EventID,
                           ImageInfo, MessageType, LocationMessageEventContent, FileInfo,
                           AudioInfo, Format, RelationType, TextMessageEventContent,
                           MediaMessageEventContent, Membership, EncryptedFile, VideoInfo,
                           MemberStateEventContent)
from mautrix.appservice import IntentAPI
from mautrix.errors import MForbidden, MNotFound, IntentError, MatrixError, SessionNotFound
from mautrix.bridge import BasePortal, NotificationDisabler, async_getter_lock
from mautrix.util.simple_lock import SimpleLock

from maufbapi.types import mqtt, graphql

from .formatter import facebook_to_matrix, matrix_to_facebook
from .config import Config
from .db import (Portal as DBPortal, Message as DBMessage, Reaction as DBReaction,
                 UserPortal as UserPortal, ThreadType)
from . import puppet as p, user as u

if TYPE_CHECKING:
    from .__main__ import MessengerBridge
    from .matrix import MatrixHandler

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from mautrix.crypto.attachments import decrypt_attachment, encrypt_attachment
except ImportError:
    decrypt_attachment = encrypt_attachment = None

geo_uri_regex: Pattern = re.compile(r"^geo:(-?\d+.\d+),(-?\d+.\d+)$")


class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


StateBridge = EventType.find("m.bridge", EventType.Class.STATE)
StateHalfShotBridge = EventType.find("uk.half-shot.bridge", EventType.Class.STATE)
MediaInfo = Union[FileInfo, VideoInfo, AudioInfo, ImageInfo]


class Portal(DBPortal, BasePortal):
    invite_own_puppet_to_pm: bool = False
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_fbid: Dict[Tuple[int, int], 'Portal'] = {}
    matrix: 'MatrixHandler'
    config: Config

    _main_intent: Optional[IntentAPI]
    _create_room_lock: asyncio.Lock
    _dedup: Deque[str]
    _oti_dedup: Dict[int, DBMessage]
    _send_locks: Dict[int, asyncio.Lock]
    _noop_lock: FakeLock = FakeLock()
    _typing: Set['u.User']
    backfill_lock: SimpleLock
    _backfill_leave: Optional[Set[IntentAPI]]

    def __init__(self, fbid: int, fb_receiver: int, fb_type: ThreadType,
                 mxid: Optional[RoomID] = None, name: Optional[str] = None,
                 photo_id: Optional[str] = None, avatar_url: Optional[ContentURI] = None,
                 encrypted: bool = False) -> None:
        super().__init__(fbid, fb_receiver, fb_type, mxid, name, photo_id, avatar_url, encrypted)
        self.log = self.log.getChild(self.fbid_log)

        self._main_intent = None
        self._create_room_lock = asyncio.Lock()
        self._dedup = deque(maxlen=100)
        self._oti_dedup = {}
        self._send_locks = {}
        self._typing = set()

        self.backfill_lock = SimpleLock("Waiting for backfilling to finish before handling %s",
                                        log=self.log)
        self._backfill_leave = None

    @classmethod
    def init_cls(cls, bridge: 'MessengerBridge') -> None:
        BasePortal.bridge = bridge
        cls.az = bridge.az
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.matrix = bridge.matrix
        cls.invite_own_puppet_to_pm = cls.config["bridge.invite_own_puppet_to_pm"]
        NotificationDisabler.puppet_cls = p.Puppet
        NotificationDisabler.config_enabled = cls.config["bridge.backfill.disable_notifications"]

    # region DB conversion

    async def delete(self) -> None:
        if self.mxid:
            await DBMessage.delete_all_by_room(self.mxid)
        self.by_fbid.pop(self.fbid_full, None)
        self.by_mxid.pop(self.mxid, None)
        await super().delete()

    # endregion
    # region Properties

    @property
    def fbid_full(self) -> Tuple[int, int]:
        return self.fbid, self.fb_receiver

    @property
    def fbid_log(self) -> str:
        if self.is_direct:
            return f"{self.fbid}<->{self.fb_receiver}"
        return str(self.fbid)

    @property
    def mqtt_key(self) -> mqtt.ThreadKey:
        if self.fb_type == ThreadType.USER:
            return mqtt.ThreadKey(other_user_id=self.fbid)
        elif self.fb_type == ThreadType.GROUP:
            return mqtt.ThreadKey(thread_fbid=self.fbid)
        else:
            raise ValueError("Unsupported thread type")

    @property
    def graphql_key(self) -> graphql.ThreadKey:
        if self.fb_type == ThreadType.USER:
            return graphql.ThreadKey(other_user_id=str(self.fbid))
        elif self.fb_type == ThreadType.GROUP:
            return graphql.ThreadKey(thread_fbid=str(self.fbid))
        else:
            raise ValueError("Unsupported thread type")

    @property
    def is_direct(self) -> bool:
        return self.fb_type == ThreadType.USER

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            raise ValueError("Portal must be postinit()ed before main_intent can be used")
        return self._main_intent

    # endregion
    # region Chat info updating

    async def update_info(self, source: Optional['u.User'] = None,
                          info: Optional[graphql.Thread] = None) -> Optional[graphql.Thread]:
        if not info:
            self.log.debug("Called update_info with no info, fetching thread info...")
            threads = await source.client.fetch_thread_info(self.fbid)
            if not threads:
                return None
            elif threads[0].thread_key.id != self.fbid:
                self.log.warning("fetch_thread_info response contained different ID (%s) "
                                 "than expected (%s)", threads[0].thread_key.id, self.fbid)
                self.log.debug(f"Number of threads in unexpected response: {len(threads)}")
            info = threads[0]
        if info.thread_key != self.graphql_key:
            self.log.warning("Got different ID (%s) than what asked for (%s) when fetching info",
                             info.thread_key.id, self.fbid)
        changed = False
        if not self.is_direct:
            changed = any(await asyncio.gather(self._update_name(info.name),
                                               self._update_photo(source, info.image),
                                               loop=self.loop))
        changed = await self._update_participants(source, info) or changed
        if changed:
            await self.update_bridge_info()
            await self.save()
        return info

    @staticmethod
    def get_photo_id(photo: Optional[Union[graphql.Picture, str]]) -> Optional[str]:
        if not photo:
            return None
        elif isinstance(photo, graphql.Picture):
            photo = photo.uri
        path = URL(photo).path
        return path[path.rfind("/") + 1:]

    @classmethod
    async def _reupload_fb_file(cls, url: str, source: 'u.User', intent: IntentAPI, *,
                                filename: Optional[str] = None, encrypt: bool = False,
                                referer: str = "messenger_thread_photo", find_size: bool = False,
                                ) -> Tuple[ContentURI, MediaInfo, Optional[EncryptedFile]]:
        if not url:
            raise ValueError("URL not provided")
        headers = {
            "referer": f"fbapp://{source.state.application.client_id}/{referer}"
        }
        async with source.client.get(url, headers=headers) as resp:
            length = int(resp.headers["Content-Length"])
            if length > cls.matrix.media_config.upload_size:
                raise ValueError("File not available: too large")
            data = await resp.read()
        mime = magic.from_buffer(data, mime=True)
        info = FileInfo(mimetype=mime, size=len(data))
        if Image and mime.startswith("image/") and find_size:
            with Image.open(BytesIO(data)) as img:
                width, height = img.size
            info = ImageInfo(mimetype=mime, size=len(data), width=width, height=height)
        upload_mime_type = mime
        decryption_info = None
        if encrypt and encrypt_attachment:
            data, decryption_info = encrypt_attachment(data)
            upload_mime_type = "application/octet-stream"
            filename = None
        url = await intent.upload_media(data, mime_type=upload_mime_type, filename=filename)
        if decryption_info:
            decryption_info.url = url
        return url, info, decryption_info

    async def _update_name(self, name: str) -> bool:
        if not name:
            self.log.warning("Got empty name in _update_name call")
            return False
        if self.name != name:
            self.log.trace("Updating name %s -> %s", self.name, name)
            self.name = name
            if self.mxid and (self.encrypted or not self.is_direct):
                await self.main_intent.set_room_name(self.mxid, self.name)
            return True
        return False

    async def _update_photo(self, source: 'u.User', photo: graphql.Picture) -> bool:
        if self.is_direct and not self.encrypted:
            return False
        photo_id = self.get_photo_id(photo)
        if self.photo_id != photo_id:
            self.photo_id = photo_id
            if photo:
                self.avatar_url = await p.Puppet.reupload_avatar(
                    source, self.main_intent, photo.uri,
                    self.fbid, use_graph=self.is_direct and (photo.height or 0) < 500)
            else:
                self.avatar_url = ContentURI("")
            if self.mxid:
                await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
            return True
        return False

    async def _update_photo_from_puppet(self, puppet: 'p.Puppet') -> bool:
        if self.photo_id == puppet.photo_id:
            return False
        self.photo_id = puppet.photo_id
        if puppet.photo_mxc:
            self.avatar_url = puppet.photo_mxc
        elif self.photo_id:
            profile = await self.main_intent.get_profile(puppet.default_mxid)
            self.avatar_url = profile.avatar_url
            puppet.photo_mxc = profile.avatar_url
        else:
            self.avatar_url = ContentURI("")
        if self.mxid:
            await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
        return True

    async def sync_per_room_nick(self, puppet: 'p.Puppet', name: str) -> None:
        intent = puppet.intent_for(self)
        content = MemberStateEventContent(membership=Membership.JOIN,
                                          avatar_url=puppet.photo_mxc,
                                          displayname=name or puppet.name)
        content[self.bridge.real_user_content_key] = True
        current_state = await intent.state_store.get_member(self.mxid, intent.mxid)
        if not current_state or current_state.displayname != content.displayname:
            self.log.debug("Syncing %s's per-room nick %s to the room",
                           puppet.fbid, content.displayname)
            await intent.send_state_event(self.mxid, EventType.ROOM_MEMBER, content,
                                          state_key=intent.mxid)

    async def _update_participants(self, source: 'u.User', info: graphql.Thread) -> bool:
        changed = False
        nick_map = info.customization_info.nickname_map if info.customization_info else {}
        for participant in info.all_participants.nodes:
            puppet = await p.Puppet.get_by_fbid(int(participant.id))
            await puppet.update_info(source, participant.messaging_actor)
            if self.is_direct and self.fbid == puppet.fbid and self.encrypted:
                changed = await self._update_name(puppet.name) or changed
                changed = await self._update_photo_from_puppet(puppet) or changed
            if self.mxid:
                if puppet.fbid != self.fb_receiver or puppet.is_real_user:
                    await puppet.intent_for(self).ensure_joined(self.mxid, bot=self.main_intent)
                if puppet.fbid in nick_map:
                    await self.sync_per_room_nick(puppet, nick_map[puppet.fbid])
        return changed

    # endregion
    # region Matrix room creation

    async def update_matrix_room(self, source: 'u.User', info: Optional[graphql.Thread] = None
                                 ) -> None:
        try:
            await self._update_matrix_room(source, info)
        except Exception:
            self.log.exception("Failed to update portal")

    async def _update_matrix_room(self, source: 'u.User', info: Optional[graphql.Thread] = None
                                  ) -> None:
        await self.main_intent.invite_user(self.mxid, source.mxid, check_cache=False)
        puppet = await p.Puppet.get_by_custom_mxid(source.mxid)
        if puppet and puppet.is_real_user:
            await puppet.intent.ensure_joined(self.mxid)

        await self.update_info(source, info)

        up = await UserPortal.get(source.fbid, self.fbid, self.fb_receiver)
        if not up:
            in_community = await source._community_helper.add_room(source._community_id, self.mxid)
            await UserPortal(user=source.fbid, portal=self.fbid, portal_receiver=self.fb_receiver,
                             in_community=in_community).insert()
        elif not up.in_community:
            up.in_community = await source._community_helper.add_room(source._community_id,
                                                                      self.mxid)
            await up.save()
        await self._sync_read_receipts(info.read_receipts.nodes)

    async def _sync_read_receipts(self, receipts: List[graphql.ReadReceipt]) -> None:
        for receipt in receipts:
            message = await DBMessage.get_closest_before(self.fbid, self.fb_receiver,
                                                         receipt.timestamp)
            if not message:
                continue
            puppet = await p.Puppet.get_by_fbid(receipt.actor.id, create=False)
            if not puppet:
                continue
            try:
                await puppet.intent_for(self).mark_read(message.mx_room, message.mxid)
            except Exception:
                self.log.warning(f"Failed to mark {message.mxid} in {message.mx_room} "
                                 f"as read by {puppet.intent.mxid}", exc_info=True)

    async def create_matrix_room(self, source: 'u.User', info: Optional[graphql.Thread] = None
                                 ) -> Optional[RoomID]:
        if self.mxid:
            try:
                await self._update_matrix_room(source, info)
            except Exception:
                self.log.exception("Failed to update portal")
            return self.mxid
        async with self._create_room_lock:
            try:
                return await self._create_matrix_room(source, info)
            except Exception:
                self.log.exception("Failed to create portal")
                return None

    @property
    def bridge_info_state_key(self) -> str:
        return f"net.maunium.facebook://facebook/{self.fbid}"

    @property
    def bridge_info(self) -> Dict[str, Any]:
        return {
            "bridgebot": self.az.bot_mxid,
            "creator": self.main_intent.mxid,
            "protocol": {
                "id": "facebook",
                "displayname": "Facebook Messenger",
                "avatar_url": self.config["appservice.bot_avatar"],
            },
            "channel": {
                "id": self.fbid,
                "displayname": self.name,
                "avatar_url": self.avatar_url,
            }
        }

    async def update_bridge_info(self) -> None:
        if not self.mxid:
            self.log.debug("Not updating bridge info: no Matrix room created")
            return
        try:
            self.log.debug("Updating bridge info...")
            await self.main_intent.send_state_event(self.mxid, StateBridge,
                                                    self.bridge_info, self.bridge_info_state_key)
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            await self.main_intent.send_state_event(self.mxid, StateHalfShotBridge,
                                                    self.bridge_info, self.bridge_info_state_key)
        except Exception:
            self.log.warning("Failed to update bridge info", exc_info=True)

    async def _create_matrix_room(self, source: 'u.User', info: Optional[graphql.Thread] = None
                                  ) -> Optional[RoomID]:
        if self.mxid:
            await self._update_matrix_room(source, info)
            return self.mxid

        self.log.debug(f"Creating Matrix room")
        name: Optional[str] = None
        initial_state = [{
            "type": str(StateBridge),
            "state_key": self.bridge_info_state_key,
            "content": self.bridge_info,
        }, {
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            "type": str(StateHalfShotBridge),
            "state_key": self.bridge_info_state_key,
            "content": self.bridge_info,
        }]
        invites = [source.mxid]
        if self.config["bridge.encryption.default"] and self.matrix.e2ee:
            self.encrypted = True
            initial_state.append({
                "type": "m.room.encryption",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            })
            if self.is_direct:
                invites.append(self.az.bot_mxid)

        info = await self.update_info(source=source, info=info)
        if not info:
            self.log.debug("update_info() didn't return info, cancelling room creation")
            return None

        if self.encrypted or not self.is_direct:
            name = self.name
            initial_state.append({"type": str(EventType.ROOM_AVATAR),
                                  "content": {"avatar_url": self.avatar_url}})
        if self.config["appservice.community_id"]:
            initial_state.append({
                "type": "m.room.related_groups",
                "content": {"groups": [self.config["appservice.community_id"]]},
            })

        # We lock backfill lock here so any messages that come between the room being created
        # and the initial backfill finishing wouldn't be bridged before the backfill messages.
        with self.backfill_lock:
            self.mxid = await self.main_intent.create_room(name=name, is_direct=self.is_direct,
                                                           initial_state=initial_state,
                                                           invitees=invites)
            if not self.mxid:
                raise Exception("Failed to create room: no mxid returned")

            if self.encrypted and self.matrix.e2ee and self.is_direct:
                try:
                    await self.az.intent.ensure_joined(self.mxid)
                except Exception:
                    self.log.warning(f"Failed to add bridge bot to new private chat {self.mxid}")

            await self.save()
            self.log.debug(f"Matrix room created: {self.mxid}")
            self.by_mxid[self.mxid] = self
            if not self.is_direct:
                await self._update_participants(source, info)
            else:
                puppet = await p.Puppet.get_by_custom_mxid(source.mxid)
                if puppet:
                    try:
                        did_join = await puppet.intent.join_room_by_id(self.mxid)
                        if did_join and self.fb_type == ThreadType.USER:
                            await source.update_direct_chats({self.main_intent.mxid: [self.mxid]})
                    except MatrixError:
                        self.log.debug("Failed to join custom puppet into newly created portal",
                                       exc_info=True)

            in_community = await source._community_helper.add_room(source._community_id, self.mxid)
            await UserPortal(user=source.fbid, portal=self.fbid, portal_receiver=self.fb_receiver,
                             in_community=in_community).upsert()

            try:
                await self.backfill(source, is_initial=True, thread=info)
            except Exception:
                self.log.exception("Failed to backfill new portal")

            await self._sync_read_receipts(info.read_receipts.nodes)

        return self.mxid

    # endregion
    # region Matrix event handling

    def require_send_lock(self, user_id: int) -> asyncio.Lock:
        try:
            lock = self._send_locks[user_id]
        except KeyError:
            lock = asyncio.Lock()
            self._send_locks[user_id] = lock
        return lock

    def optional_send_lock(self, user_id: int) -> Union[asyncio.Lock, FakeLock]:
        try:
            return self._send_locks[user_id]
        except KeyError:
            pass
        return self._noop_lock

    async def _send_delivery_receipt(self, event_id: EventID) -> None:
        if event_id and self.config["bridge.delivery_receipts"]:
            try:
                await self.az.intent.mark_read(self.mxid, event_id)
            except Exception:
                self.log.exception("Failed to send delivery receipt for %s", event_id)

    async def _send_bridge_error(self, msg: str) -> None:
        await self._send_message(self.main_intent, TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            body=f"\u26a0 Your message may not have been bridged: {msg}"))

    async def handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                    event_id: EventID) -> None:
        # TODO handle errors?
        # try:
        await self._handle_matrix_message(sender, message, event_id)
        # except fbchat.PleaseRefresh:
        #     self.log.debug(f"Got PleaseRefresh error while trying to bridge {event_id}")
        #     await sender.refresh()
        #     try:
        #         await self._handle_matrix_message(sender, message, event_id)
        #     except fbchat.FacebookError as e:
        #         self.log.exception(f"Got FacebookError while trying to bridge {event_id} "
        #                            "after auto-refreshing")
        #         await self._send_bridge_error(getattr(e, "description", e.message))
        # except fbchat.FacebookError as e:
        #     self.log.exception(f"Got FacebookError while trying to bridge {event_id}")
        #     await self._send_bridge_error(getattr(e, "description", e.message))

    async def _handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                     event_id: EventID) -> None:
        if ((message.get(self.az.real_user_content_key, False)
             and await p.Puppet.get_by_custom_mxid(sender.mxid))):
            self.log.debug(f"Ignoring puppet-sent message by confirmed puppet user {sender.mxid}")
            return
        if message.msgtype == MessageType.TEXT or message.msgtype == MessageType.NOTICE:
            await self._handle_matrix_text(event_id, sender, message)
        elif message.msgtype.is_media:
            await self._handle_matrix_media(event_id, sender, message)
        # elif message.msgtype == MessageType.LOCATION:
        #     await self._handle_matrix_location(sender, message)
        else:
            self.log.warning(f"Unsupported msgtype {message.msgtype} in {event_id}")
            return

    async def _make_dbm(self, sender: 'u.User', event_id: EventID) -> DBMessage:
        oti = sender.mqtt.generate_offline_threading_id()
        dbm = DBMessage(mxid=event_id, mx_room=self.mxid, fb_txn_id=oti, index=0,
                        fb_chat=self.fbid, fb_receiver=self.fb_receiver, fb_sender=sender.fbid,
                        timestamp=int(time.time() * 1000), fbid=None)
        self._oti_dedup[oti] = dbm
        await dbm.insert()
        return dbm

    async def _handle_matrix_text(self, event_id: EventID, sender: 'u.User',
                                  message: TextMessageEventContent) -> None:
        converted = await matrix_to_facebook(message, self.mxid, self.log)
        dbm = await self._make_dbm(sender, event_id)
        resp = await sender.mqtt.send_message(self.fbid, self.fb_type != ThreadType.USER,
                                              message=converted.text, mentions=converted.mentions,
                                              reply_to=converted.reply_to,
                                              offline_threading_id=dbm.fb_txn_id)
        if not resp.success and resp.error_message:
            self.log.debug(f"Error handling Matrix message {event_id}: {resp.error_message}")
            await self._send_bridge_error(resp.error_message)
        else:
            self.log.debug(f"Handled Matrix message {event_id} -> OTI: {dbm.fb_txn_id}")
            await self._send_delivery_receipt(event_id)

    async def _handle_matrix_media(self, event_id: EventID, sender: 'u.User',
                                   message: MediaMessageEventContent) -> None:
        if message.file and decrypt_attachment:
            data = await self.main_intent.download_media(message.file.url)
            data = decrypt_attachment(data, message.file.key.key,
                                      message.file.hashes.get("sha256"), message.file.iv)
        elif message.url:
            data = await self.main_intent.download_media(message.url)
        else:
            return None
        mime = message.info.mimetype or magic.from_buffer(data, mime=True)
        dbm = await self._make_dbm(sender, event_id)
        reply_to = None
        if message.relates_to.rel_type == RelationType.REPLY:
            reply_to_msg = await DBMessage.get_by_mxid(message.relates_to.event_id, self.mxid)
            if reply_to_msg:
                reply_to = reply_to_msg.fbid
            else:
                self.log.warning(f"Couldn't find reply target {message.relates_to.event_id}"
                                 " to bridge media message reply metadata to Facebook")
        # await sender.mqtt.opened_thread(self.fbid)
        resp = await sender.client.send_media(data, message.body, mime,
                                              offline_threading_id=dbm.fb_txn_id,
                                              reply_to=reply_to, chat_id=self.fbid,
                                              is_group=self.fb_type != ThreadType.USER)
        if not resp.media_id and resp.debug_info:
            self.log.debug(f"Error uploading media for Matrix message {event_id}: "
                           f"{resp.debug_info.message}")
            await self._send_bridge_error(f"Media upload error: {resp.debug_info.message}")
            return
        await self._send_delivery_receipt(event_id)
        try:
            self._oti_dedup.pop(dbm.fb_txn_id)
        except KeyError:
            self.log.trace(f"Message ID for OTI {dbm.fb_txn_id} seems to have been found already")
        else:
            dbm.fbid = resp.message_id
            # TODO can we find the timestamp?
            await dbm.update()
        self.log.debug(f"Handled Matrix message {event_id} -> {resp.message_id} / {dbm.fb_txn_id}")

    async def _handle_matrix_location(self, sender: 'u.User',
                                      message: LocationMessageEventContent) -> str:
        pass
        # TODO
        # match = geo_uri_regex.fullmatch(message.geo_uri)
        # return await self.thread_for(sender).send_pinned_location(float(match.group(1)),
        #                                                           float(match.group(2)))

    async def handle_matrix_redaction(self, sender: 'u.User', event_id: EventID,
                                      redaction_event_id: EventID) -> None:
        if not self.mxid:
            return

        message = await DBMessage.get_by_mxid(event_id, self.mxid)
        if message:
            try:
                await message.delete()
                await sender.client.unsend(message.fbid)
                await self._send_delivery_receipt(redaction_event_id)
            except Exception:
                self.log.exception("Unsend failed")
            return

        reaction = await DBReaction.get_by_mxid(event_id, self.mxid)
        if reaction:
            try:
                await reaction.delete()
                await sender.client.react(reaction.fb_msgid, None)
                await self._send_delivery_receipt(redaction_event_id)
            except Exception:
                self.log.exception("Removing reaction failed")

    async def handle_matrix_reaction(self, sender: 'u.User', event_id: EventID,
                                     reacting_to: EventID, reaction: str) -> None:
        # Facebook doesn't use variation selectors, Matrix does
        reaction = reaction.rstrip("\ufe0f")

        async with self.require_send_lock(sender.fbid):
            message = await DBMessage.get_by_mxid(reacting_to, self.mxid)
            if not message:
                self.log.debug(f"Ignoring reaction to unknown event {reacting_to}")
                return

            existing = await DBReaction.get_by_fbid(message.fbid, self.fb_receiver, sender.fbid)
            if existing and existing.reaction == reaction:
                return

            await sender.client.react(message.fbid, reaction)
            await self._upsert_reaction(existing, self.main_intent, event_id, message, sender,
                                        reaction)
        await self._send_delivery_receipt(event_id)

    async def handle_matrix_leave(self, user: 'u.User') -> None:
        if self.is_direct:
            self.log.info(f"{user.mxid} left private chat portal with {self.fbid}")
            if not user.is_outbound and user.fbid == self.fb_receiver:
                self.log.info(f"{user.mxid} was the recipient of this portal. "
                              "Cleaning up and deleting...")
                await self.cleanup_and_delete()
        else:
            self.log.debug(f"{user.mxid} left portal to {self.fbid}")

    async def handle_matrix_typing(self, users: Set['u.User']) -> None:
        # FIXME
        pass
        # stopped_typing = [self.thread_for(user).stop_typing() for user in self._typing - users]
        # started_typing = [self.thread_for(user).start_typing() for user in users - self._typing]
        # self._typing = users
        # await asyncio.gather(*stopped_typing, *started_typing, loop=self.loop)

    async def enable_dm_encryption(self) -> bool:
        ok = await super().enable_dm_encryption()
        if ok:
            try:
                puppet = await p.Puppet.get_by_fbid(self.fbid)
                await self.main_intent.set_room_name(self.mxid, puppet.name)
            except Exception:
                self.log.warning(f"Failed to set room name", exc_info=True)
        return ok

    # endregion
    # region Facebook event handling

    async def _bridge_own_message_pm(self, source: 'u.User', sender: 'p.Puppet', mid: str,
                                     invite: bool = True) -> bool:
        if self.is_direct and sender.fbid == source.fbid and not sender.is_real_user:
            if self.invite_own_puppet_to_pm and invite:
                await self.main_intent.invite_user(self.mxid, sender.mxid)
            elif await self.az.state_store.get_membership(self.mxid,
                                                          sender.mxid) != Membership.JOIN:
                self.log.warning(f"Ignoring own {mid} in private chat because own puppet is not in"
                                 " room.")
                return False
        return True

    async def _add_facebook_reply(self, content: MessageEventContent,
                                  reply_to: Union[graphql.MinimalMessage, mqtt.Message]) -> None:
        if isinstance(reply_to, graphql.MinimalMessage):
            message = await DBMessage.get_by_fbid(reply_to.message_id, self.fb_receiver)
        elif isinstance(reply_to, mqtt.Message):
            meta = reply_to.metadata
            message = await DBMessage.get_by_fbid_or_oti(meta.id, meta.offline_threading_id,
                                                         self.fb_receiver, meta.sender)
            if not message.fbid:
                self.log.debug(f"Got message ID {meta.id} for offline threading ID "
                               f"{message.fb_txn_id} / {message.mxid} (in database) from reply")
                message.fbid = meta.id
                message.timestamp = meta.timestamp
                await message.update()
        else:
            return

        if not message:
            self.log.warning(f"Couldn't find reply target {reply_to}"
                             " to bridge reply metadata to Matrix")
            return

        content.set_reply(message.mxid)
        if not isinstance(content, TextMessageEventContent):
            return

        try:
            evt = await self.main_intent.get_event(message.mx_room, message.mxid)
        except (MNotFound, MForbidden):
            evt = None
        if not evt:
            return

        if evt.type == EventType.ROOM_ENCRYPTED:
            try:
                evt = await self.matrix.e2ee.decrypt(evt, wait_session_timeout=0)
            except SessionNotFound:
                return

        if isinstance(evt.content, TextMessageEventContent):
            evt.content.trim_reply_fallback()

        content.set_reply(evt)

    async def handle_facebook_message(self, source: 'u.User', sender: 'p.Puppet',
                                      message: Union[graphql.Message, mqtt.Message],
                                      reply_to: Optional[mqtt.Message] = None) -> None:
        try:
            await self._handle_facebook_message(source, sender, message, reply_to)
        except Exception:
            self.log.exception("Error handling Facebook message %s",
                               message.message_id if isinstance(message, graphql.Message)
                               else message.metadata.id)

    async def _handle_facebook_message(self, source: 'u.User', sender: 'p.Puppet',
                                       message: Union[graphql.Message, mqtt.Message],
                                       reply_to: Optional[mqtt.Message] = None) -> None:
        if isinstance(message, graphql.Message):
            self.log.trace("Facebook GraphQL event content: %s", message)
            msg_id = message.message_id
            oti = int(message.offline_threading_id)
            timestamp = message.timestamp
        elif isinstance(message, mqtt.Message):
            self.log.trace("Facebook MQTT event content: %s", message)
            msg_id = message.metadata.id
            oti = message.metadata.offline_threading_id
            timestamp = message.metadata.timestamp
        else:
            raise ValueError(f"Invalid message class {type(message).__name__}")

        # Check in-memory queues for duplicates
        if oti in self._oti_dedup:
            dbm = self._oti_dedup.pop(oti)
            self.log.debug(f"Got message ID {msg_id} for offline threading ID {oti} / {dbm.mxid}"
                           " (in dedup queue)")
            self._dedup.appendleft(msg_id)
            dbm.fbid = msg_id
            dbm.timestamp = timestamp
            await dbm.update()
            return
        elif msg_id in self._dedup:
            self.log.trace("Not handling message %s, found ID in dedup queue", msg_id)
            return

        # Check database for duplicates
        dbm = await DBMessage.get_by_fbid_or_oti(msg_id, oti, self.fb_receiver, sender.fbid)
        if dbm:
            if not dbm.fbid:
                self.log.debug(f"Got message ID {msg_id} for offline threading ID {dbm.fb_txn_id} "
                               f"/ {dbm.mxid} (in database)")
                dbm.fbid = msg_id
                dbm.timestamp = timestamp
                await dbm.update()
            else:
                self.log.debug(f"Not handling message {msg_id}, found duplicate in database")
            return

        self.log.debug(f"Handling Facebook event {msg_id} (/{oti})")
        self._dedup.appendleft(msg_id)
        if not self.mxid:
            mxid = await self.create_matrix_room(source)
            if not mxid:
                # Failed to create
                return
        if not await self._bridge_own_message_pm(source, sender, f"message {msg_id}"):
            return
        intent = sender.intent_for(self)
        if ((self._backfill_leave is not None and self.fbid != sender.fbid
             and intent != sender.intent and intent not in self._backfill_leave)):
            self.log.debug("Adding %s's default puppet to room for backfilling", sender.mxid)
            await self.main_intent.invite_user(self.mxid, intent.mxid)
            await intent.ensure_joined(self.mxid)
            self._backfill_leave.add(intent)
        if isinstance(message, graphql.Message):
            event_ids = await self._handle_graphql_message(source, intent, message)
        else:
            event_ids = await self._handle_mqtt_message(source, intent, message, reply_to)
        if not event_ids:
            self.log.warning(f"Unhandled Messenger message {msg_id}")
            return
        event_ids = [event_id for event_id in event_ids if event_id]
        self.log.debug(f"Handled Messenger message {msg_id} -> {event_ids}")
        await DBMessage.bulk_create(fbid=msg_id, oti=oti, fb_chat=self.fbid, fb_sender=sender.fbid,
                                    fb_receiver=self.fb_receiver, mx_room=self.mxid,
                                    timestamp=timestamp, event_ids=event_ids)
        await self._send_delivery_receipt(event_ids[-1])

    async def _handle_mqtt_message(self, source: 'u.User', intent: IntentAPI,
                                   message: mqtt.Message, reply_to: Optional[mqtt.Message]
                                   ) -> List[EventID]:
        event_ids = []
        if message.sticker:
            event_ids.append(await self._handle_facebook_sticker(
                source, intent, message.sticker, reply_to, message.metadata.timestamp
            ))
        if len(message.attachments) > 0:
            attach_ids = await asyncio.gather(
                *[self._handle_facebook_attachment(message.metadata.id, source, intent, attachment,
                                                   reply_to, message.metadata.timestamp,
                                                   message_text=message.text)
                  for attachment in message.attachments]
            )
            event_ids += [attach_id for attach_id in attach_ids if attach_id]
        if message.text:
            event_ids.append(await self._handle_facebook_text(intent, message, reply_to,
                                                              message.metadata.timestamp))
        return event_ids

    async def _convert_extensible_media(self, source: 'u.User', intent: IntentAPI,
                                        sa: graphql.StoryAttachment, message_text: str,
                                        ) -> Optional[MessageEventContent]:
        if sa.target and sa.target.typename == graphql.AttachmentType.EXTERNAL_URL:
            url = str(sa.clean_url)
            if message_text is not None and url in message_text:
                # URL is present in message, don't repost
                return None
            escaped_url = escape(url)
            html = f'<a href="{escaped_url}">{escaped_url}</a>'
            return TextMessageEventContent(msgtype=MessageType.TEXT, format=Format.HTML,
                                           body=str(sa.clean_url), formatted_body=html)
        elif sa.media:
            msgtype = {
                "Image": MessageType.IMAGE,
                "Video": MessageType.VIDEO,
            }.get(sa.media.typename_str)
            if sa.media.playable_url and msgtype == MessageType.VIDEO:
                info = VideoInfo()
                url = sa.media.playable_url
            elif sa.media.image_natural and msgtype == MessageType.IMAGE:
                url = sa.media.image_natural.uri
                info = ImageInfo(width=sa.media.image_natural.width,
                                 height=sa.media.image_natural.height)
            else:
                self.log.debug("Unsupported story media attachment: %s",
                               sa.serialize())
                body = "Unsupported shared media attachment"
                html = body
                if sa.title:
                    body = f"{body}: **{sa.title}**"
                    html = f"{html}: <strong>{escape(sa.title)}</strong>"
                if sa.description:
                    body = f"{body}\n\n>{sa.description.text}"
                    html = (f"<p>{html}</p>"
                            f"<blockquote>{escape(sa.description.text)}</blockquote>")
                return TextMessageEventContent(msgtype=MessageType.TEXT, format=Format.HTML,
                                               external_url=sa.url, body=body,
                                               formatted_body=html)
            try:
                mxc, additional_info, decryption_info = await self._reupload_fb_file(
                    url, source, intent, encrypt=self.encrypted, find_size=False)
            except ValueError as e:
                self.log.debug("Failed to reupload story attachment media", exc_info=True)
                return TextMessageEventContent(msgtype=MessageType.NOTICE, body=f"{e}\n{sa.url}",
                                               external_url=sa.url)
            info.size = additional_info.size
            info.mimetype = additional_info.mimetype
            filename = f"{sa.media.typename_str}{mimetypes.guess_extension(info.mimetype)}"
            return MediaMessageEventContent(url=mxc, file=decryption_info, msgtype=msgtype,
                                            body=filename, info=info, external_url=sa.url)
        else:
            self.log.debug("Unhandled story attachment: %s",
                           sa.serialize())
            return None

    async def _convert_mqtt_attachment(self, msg_id: str, source: 'u.User', intent: IntentAPI,
                                       attachment: mqtt.Attachment, message_text: str
                                       ) -> MessageEventContent:
        filename = attachment.file_name
        if attachment.mime_type and "." not in filename:
            filename += mimetypes.guess_extension(attachment.mime_type)
        referer = "unknown"
        if attachment.extensible_media:
            sa = attachment.parse_extensible().story_attachment
            self.log.trace("Story attachment %s content: %s", attachment.media_id_str, sa)
            return await self._convert_extensible_media(source, intent, sa,
                                                        message_text=message_text)
        elif attachment.video_info:
            msgtype = MessageType.VIDEO
            url = attachment.video_info.download_url
            info = VideoInfo(duration=attachment.video_info.duration_ms,
                             width=attachment.video_info.original_width,
                             height=attachment.video_info.original_height)
        elif attachment.audio_info:
            msgtype = MessageType.AUDIO
            url = attachment.audio_info.url
            info = AudioInfo(duration=attachment.audio_info.duration_ms)
        elif attachment.image_info:
            referer = "messenger_thread_photo"
            msgtype = MessageType.IMAGE
            info = ImageInfo(width=attachment.image_info.original_width,
                             height=attachment.image_info.original_height)
            if attachment.image_info.alt_previews:
                url = list(attachment.image_info.alt_previews.values())[0]
                # Override the mime type or detect from file
                attachment.mime_type = {
                    "webp": "image/webp",
                    "gif": "image/gif",
                    "png": "image/png",
                }.get(attachment.image_info.alt_preview_type, None)
            else:
                url = list(attachment.image_info.previews.values())[0]
            # TODO find out if we need to use get_image_url in some cases even with MQTT
            # url = await source.client.get_image_url(msg_id, attachment.media_id)
        elif attachment.media_id:
            # TODO what if it's not a file?
            msgtype = MessageType.FILE
            url = await source.client.get_file_url(self.fbid, msg_id, attachment.media_id)
            info = FileInfo()
        else:
            msg = f"Unsupported attachment"
            self.log.warning(msg)
            return TextMessageEventContent(msgtype=MessageType.NOTICE, body=msg)
        mxc, additional_info, decryption_info = await self._reupload_fb_file(
            url, source, intent, filename=filename, encrypt=self.encrypted,
            find_size=False, referer=referer)
        info.size = additional_info.size
        info.mimetype = attachment.mime_type or additional_info.mimetype
        return MediaMessageEventContent(url=mxc, file=decryption_info, msgtype=msgtype,
                                        body=filename, info=info)

    async def _handle_graphql_message(self, source: 'u.User', intent: IntentAPI,
                                      message: graphql.Message) -> List[EventID]:
        reply_to_msg = (message.replied_to_message.message
                        if message.replied_to_message else None)
        event_ids = []
        if message.sticker:
            event_ids.append(await self._handle_facebook_sticker(
                source, intent, int(message.sticker.id), reply_to_msg, message.timestamp
            ))
        if len(message.blob_attachments) > 0:
            attach_ids = await asyncio.gather(
                *[self._handle_facebook_attachment(message.message_id, source, intent, attachment,
                                                   reply_to_msg, message.timestamp)
                  for attachment in message.blob_attachments]
            )
            event_ids += [attach_id for attach_id in attach_ids if attach_id]
        text = message.message.text if message.message else None
        if message.extensible_attachment:
            sa = message.extensible_attachment.story_attachment
            content = await self._convert_extensible_media(source, intent, sa,
                                                           message_text=text)
            if content:
                event_ids.append(await self._send_message(intent, content,
                                                          timestamp=message.timestamp))
        if text:
            event_ids.append(await self._handle_facebook_text(intent, message.message,
                                                              reply_to_msg, message.timestamp))
        return event_ids

    async def _handle_facebook_text(self, intent: IntentAPI,
                                    message: Union[graphql.MessageText, mqtt.Message],
                                    reply_to: Union[graphql.MinimalMessage, mqtt.Message],
                                    timestamp: int) -> EventID:
        content = await facebook_to_matrix(message)
        await self._add_facebook_reply(content, reply_to)
        return await self._send_message(intent, content, timestamp=timestamp)

    async def _handle_facebook_sticker(self, source: 'u.User', intent: IntentAPI, sticker_id: int,
                                       reply_to: Union[graphql.MinimalMessage, mqtt.Message],
                                       timestamp: int) -> EventID:
        resp = await source.client.fetch_stickers([sticker_id], sticker_labels_enabled=True)
        sticker = resp.nodes[0]
        url = (sticker.animated_image or sticker.thread_image).uri
        mxc, info, decryption_info = await self._reupload_fb_file(url, source, intent,
                                                                  encrypt=self.encrypted,
                                                                  find_size=True)
        content = MediaMessageEventContent(url=mxc, file=decryption_info, info=info,
                                           msgtype=MessageType.STICKER, body=sticker.label or "")
        await self._add_facebook_reply(content, reply_to)
        return await self._send_message(intent, event_type=EventType.STICKER, content=content,
                                        timestamp=timestamp)

    async def _handle_facebook_attachment(self, msg_id: str, source: 'u.User', intent: IntentAPI,
                                          attachment: Union[graphql.Attachment, mqtt.Attachment],
                                          reply_to: Union[graphql.MinimalMessage, mqtt.Message],
                                          timestamp: int, message_text: Optional[str] = None
                                          ) -> Optional[EventID]:
        if isinstance(attachment, graphql.Attachment):
            content = await self._convert_graphql_attachment(msg_id, source, intent, attachment)
        elif isinstance(attachment, mqtt.Attachment):
            content = await self._convert_mqtt_attachment(msg_id, source, intent, attachment,
                                                          message_text=message_text)
        else:
            raise ValueError(f"Invalid attachment type {type(attachment).__name__}")
        if not content:
            return None
        await self._add_facebook_reply(content, reply_to)
        return await self._send_message(intent, content, timestamp=timestamp)

    async def _convert_graphql_attachment(self, msg_id: str, source: 'u.User', intent: IntentAPI,
                                          attachment: graphql.Attachment) -> MessageEventContent:
        filename = attachment.filename
        if attachment.mimetype and "." not in filename:
            filename += mimetypes.guess_extension(attachment.mimetype)
        referer = "unknown"
        if attachment.typename in (graphql.AttachmentType.IMAGE,
                                   graphql.AttachmentType.ANIMATED_IMAGE):
            msgtype = MessageType.IMAGE
            if attachment.typename == graphql.AttachmentType.IMAGE:
                info = ImageInfo(width=attachment.original_dimensions.x,
                                 height=attachment.original_dimensions.y,
                                 mimetype=attachment.mimetype)
                full_screen = attachment.image_full_screen
            else:
                info = ImageInfo(width=attachment.animated_image_original_dimensions.x,
                                 height=attachment.animated_image_original_dimensions.y,
                                 mimetype=attachment.mimetype)
                full_screen = attachment.animated_image_full_screen
            url = full_screen.uri
            if (info.width, info.height) > full_screen.dimensions:
                url = await source.client.get_image_url(msg_id, attachment.attachment_fbid) or url
            referer = "messenger_thread_photo"
        elif attachment.typename == graphql.AttachmentType.AUDIO:
            msgtype = MessageType.AUDIO
            info = AudioInfo(duration=attachment.playable_duration_in_ms,
                             mimetype=attachment.mimetype)
            url = attachment.playable_url
        elif attachment.typename == graphql.AttachmentType.VIDEO:
            msgtype = MessageType.VIDEO
            info = VideoInfo(duration=attachment.playable_duration_in_ms,
                             mimetype=attachment.mimetype)
            url = attachment.attachment_video_url
        elif attachment.typename == graphql.AttachmentType.FILE:
            msgtype = MessageType.FILE
            url = await source.client.get_file_url(self.fbid, msg_id, attachment.attachment_fbid)
            info = FileInfo(mimetype=attachment.mimetype)
        else:
            # TODO location attachments
            msg = f"Unsupported attachment type {attachment.typename}"
            self.log.warning(msg)
            return TextMessageEventContent(msgtype=MessageType.NOTICE, body=msg)
        mxc, additional_info, decryption_info = await self._reupload_fb_file(
            url, source, intent, filename=filename, encrypt=self.encrypted,
            find_size=False, referer=referer)
        info.size = additional_info.size
        return MediaMessageEventContent(url=mxc, file=decryption_info, msgtype=msgtype,
                                        body=filename, info=info)

    async def _convert_facebook_location(
        self, source: 'u.User', intent: IntentAPI, location: graphql.StoryTarget
    ) -> Union[LocationMessageEventContent, TextMessageEventContent]:
        long, lat = location.coordinates.longitude, location.coordinates.latitude
        if not long or not lat:
            # if location.address or location.url:
            #     self.log.trace("Location message with no coordinates: %s", location)
            #     return TextMessageEventContent(msgtype=MessageType.TEXT,
            #                                    body=f"{location.address}\n{location.url}")
            # else:
            self.log.warning("Unsupported Facebook location message content: %s", location)
            return TextMessageEventContent(msgtype=MessageType.NOTICE,
                                           body="Location message with unsupported content")
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        geo = f"{round(lat, 6)},{round(long, 6)}"

        text = f"{round(abs(lat), 4)}° {lat_char}, {round(abs(long), 4)}° {long_char}"
        url = f"https://maps.google.com/?q={geo}"

        content = LocationMessageEventContent(body=f"Location: {text}\n{url}",
                                              geo_uri=f"geo:{lat},{long}",
                                              msgtype=MessageType.LOCATION)
        # Some clients support formatted body in m.location, so add that as well.
        content["format"] = str(Format.HTML)
        content["formatted_body"] = f"<p>Location: <a href='{url}'>{text}</a></p"
        # TODO find out if locations still have addresses
        # if location.address:
        #     content.body = f"{location.address}\n{content.body}"
        #     content["formatted_body"] = f"<p>{location.address}</p>{content['formatted_body']}"
        return content

    async def handle_facebook_unsend(self, sender: 'p.Puppet', message_id: str, timestamp: int
                                     ) -> None:
        if not self.mxid:
            return
        for message in await DBMessage.get_all_by_fbid(message_id, self.fb_receiver):
            try:
                await sender.intent_for(self).redact(message.mx_room, message.mxid,
                                                     timestamp=timestamp)
            except MForbidden:
                await self.main_intent.redact(message.mx_room, message.mxid,
                                              timestamp=timestamp)
            await message.delete()

    async def handle_facebook_seen(self, source: 'u.User', sender: 'p.Puppet', timestamp: int
                                   ) -> None:
        if not self.mxid:
            return
        msg = await DBMessage.get_closest_before(self.fbid, self.fb_receiver, timestamp)
        if not msg:
            return
        if not await self._bridge_own_message_pm(source, sender, "read receipt",
                                                 invite=False):
            return
        # TODO can we set a timestamp when the read receipt happened?
        await sender.intent_for(self).mark_read(msg.mx_room, msg.mxid)
        self.log.debug(f"Handled Messenger read receipt from {sender.fbid} "
                       f"up to {timestamp}/{msg.mxid}")

    async def handle_facebook_typing(self, source: 'u.User', sender: 'p.Puppet') -> None:
        if not await self._bridge_own_message_pm(source, sender, "typing notification",
                                                 invite=False):
            return
        await sender.intent.set_typing(self.mxid, is_typing=True)

    async def handle_facebook_photo(self, source: 'u.User', sender: 'p.Puppet',
                                    new_photo: mqtt.Attachment, message_id: str, timestamp: int
                                    ) -> None:
        if not self.mxid or self.is_direct or message_id in self._dedup:
            return
        self._dedup.appendleft(message_id)
        photo_url = await source.client.get_image_url(message_id, new_photo.media_id)
        if not photo_url and new_photo.image_info.previews:
            photo_url = list(new_photo.image_info.previews.values())[-1]
        photo_id = self.get_photo_id(photo_url)
        if self.photo_id == photo_id:
            return
        self.photo_id = photo_id
        self.avatar_url, *_ = await self._reupload_fb_file(photo_url, source, sender.intent)
        try:
            event_id = await sender.intent.set_room_avatar(self.mxid, self.avatar_url)
        except IntentError:
            event_id = await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
        await DBMessage(mxid=event_id, mx_room=self.mxid, index=0, timestamp=timestamp,
                        fbid=message_id, fb_chat=self.fbid, fb_receiver=self.fb_receiver).insert()
        await self.update_bridge_info()

    async def handle_facebook_name(self, source: 'u.User', sender: 'p.Puppet', new_name: str,
                                   message_id: str, timestamp: int) -> None:
        if self.name == new_name or message_id in self._dedup:
            return
        self._dedup.appendleft(message_id)
        self.name = new_name
        if not self.mxid or self.is_direct:
            return
        try:
            event_id = await sender.intent.set_room_name(self.mxid, self.name)
        except IntentError:
            event_id = await self.main_intent.set_room_name(self.mxid, self.name)
        await DBMessage(mxid=event_id, mx_room=self.mxid, index=0, timestamp=timestamp,
                        fbid=message_id, fb_chat=self.fbid, fb_receiver=self.fb_receiver).insert()
        await self.update_bridge_info()

    async def handle_facebook_reaction_add(self, source: 'u.User', sender: 'p.Puppet',
                                           message_id: str, reaction: str) -> None:
        dedup_id = f"react_{message_id}_{sender}_{reaction}"
        async with self.optional_send_lock(sender.fbid):
            if dedup_id in self._dedup:
                return
            self._dedup.appendleft(dedup_id)

        existing = await DBReaction.get_by_fbid(message_id, self.fb_receiver, sender.fbid)
        if existing and existing.reaction == reaction:
            return

        if not await self._bridge_own_message_pm(source, sender, f"reaction to {message_id}"):
            return

        intent = sender.intent_for(self)

        message = await DBMessage.get_by_fbid(message_id, self.fb_receiver)
        if not message:
            self.log.debug(f"Ignoring reaction to unknown message {message_id}")
            return

        matrix_reaction = reaction
        # TODO there are probably other emojis that need variation selectors
        if reaction in ("\u2764", "\U0001f44d", "\U0001f44e"):
            matrix_reaction += "\ufe0f"
        mxid = await intent.react(message.mx_room, message.mxid, matrix_reaction)
        self.log.debug(f"Reacted to {message.mxid}, got {mxid}")

        await self._upsert_reaction(existing, intent, mxid, message, sender, reaction)

    async def _upsert_reaction(self, existing: DBReaction, intent: IntentAPI, mxid: EventID,
                               message: DBMessage, sender: Union['u.User', 'p.Puppet'],
                               reaction: str) -> None:
        if existing:
            self.log.debug(f"_upsert_reaction redacting {existing.mxid} and inserting {mxid}"
                           f" (message: {message.mxid})")
            await intent.redact(existing.mx_room, existing.mxid)
            existing.reaction = reaction
            existing.mxid = mxid
            existing.mx_room = message.mx_room
            await existing.save()
        else:
            self.log.debug(f"_upsert_reaction inserting {mxid} (message: {message.mxid})")
            await DBReaction(mxid=mxid, mx_room=message.mx_room, fb_msgid=message.fbid,
                             fb_receiver=self.fb_receiver, fb_sender=sender.fbid,
                             reaction=reaction).insert()

    async def handle_facebook_reaction_remove(self, source: 'u.User', sender: 'p.Puppet',
                                              message_id: str) -> None:
        if not self.mxid:
            return
        reaction = await DBReaction.get_by_fbid(message_id, self.fb_receiver, sender.fbid)
        if reaction:
            try:
                await sender.intent_for(self).redact(reaction.mx_room, reaction.mxid)
            except MForbidden:
                await self.main_intent.redact(reaction.mx_room, reaction.mxid)
            await reaction.delete()

    async def handle_facebook_join(self, source: 'u.User', sender: 'p.Puppet',
                                   users: List['p.Puppet']) -> None:
        sender_intent = sender.intent_for(self)
        for user in users:
            await sender_intent.invite_user(self.mxid, user.mxid)
            await user.intent_for(self).join_room_by_id(self.mxid)

    async def handle_facebook_leave(self, source: 'u.User', sender: 'p.Puppet', removed: 'p.Puppet'
                                    ) -> None:
        if sender == removed:
            await removed.intent_for(self).leave_room(self.mxid)
        else:
            try:
                await sender.intent_for(self).kick_user(self.mxid, removed.mxid)
            except MForbidden:
                await self.main_intent.kick_user(self.mxid, removed.mxid,
                                                 reason=f"Kicked by {sender.name}")

    # endregion

    async def backfill(self, source: 'u.User', is_initial: bool, thread: graphql.Thread) -> None:
        limit = (self.config["bridge.backfill.initial_limit"] if is_initial
                 else self.config["bridge.backfill.missed_limit"])
        if limit == 0:
            return
        elif limit < 0:
            limit = None
        last_active = None
        if not is_initial and thread and len(thread.last_message.nodes) > 0:
            last_active = thread.last_message.nodes[0].timestamp
        most_recent = await DBMessage.get_most_recent(self.fbid, self.fb_receiver)
        if most_recent and is_initial:
            self.log.debug("Not backfilling %s: already bridged messages found", self.fbid_log)
        elif (not most_recent or not most_recent.timestamp) and not is_initial:
            self.log.debug("Not backfilling %s: no most recent message found", self.fbid_log)
        elif last_active and most_recent.timestamp >= last_active:
            self.log.debug("Not backfilling %s: last activity is equal to most recent bridged "
                           "message (%s >= %s)", self.fbid_log, most_recent.timestamp, last_active)
        else:
            with self.backfill_lock:
                await self._backfill(source, limit, most_recent.timestamp if most_recent else None,
                                     thread=thread)

    async def _backfill(self, source: 'u.User', limit: int, after_timestamp: Optional[int],
                        thread: graphql.Thread) -> None:
        self.log.debug("Backfilling history through %s", source.mxid)
        messages = thread.messages.nodes
        oldest_message = messages[0]
        before_timestamp = oldest_message.timestamp - 1
        self.log.debug("Fetching up to %d messages through %s", limit, source.fbid)
        while len(messages) < limit:
            resp = await source.client.fetch_messages(self.fbid, before_timestamp)
            if not resp.nodes:
                self.log.debug("Stopping fetching messages at %s after empty response",
                               oldest_message.message_id)
                break
            oldest_message = resp.nodes[0]
            before_timestamp = oldest_message.timestamp - 1
            messages = resp.nodes + messages
            if not resp.page_info.has_previous_page:
                self.log.debug("Stopping fetching messages at %s as response said there are no "
                               "more messages", oldest_message.message_id)
                break
            elif after_timestamp and oldest_message.timestamp <= after_timestamp:
                self.log.debug("Stopping fetching messages at %s as message is older than newest "
                               "bridged message (%s < %s)", oldest_message.message_id,
                               oldest_message.timestamp, after_timestamp)
                break
        if after_timestamp:
            try:
                slice_index = next(index for index, message in enumerate(messages)
                                   if message.timestamp > after_timestamp)
                messages = messages[slice_index:]
            except StopIteration:
                messages = []
        if not messages:
            self.log.debug("Didn't get any messages from server")
            return
        self.log.debug("Got %d messages from server", len(messages))
        self._backfill_leave = set()
        async with NotificationDisabler(self.mxid, source):
            for message in messages:
                puppet = await p.Puppet.get_by_fbid(message.message_sender.id)
                await self.handle_facebook_message(source, puppet, message)
        for intent in self._backfill_leave:
            self.log.trace("Leaving room with %s post-backfill", intent.mxid)
            await intent.leave_room(self.mxid)
        self.log.info("Backfilled %d messages through %s", len(messages), source.mxid)

    # region Database getters

    async def postinit(self) -> None:
        self.by_fbid[self.fbid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self
        self._main_intent = ((await p.Puppet.get_by_fbid(self.fbid)).default_mxid_intent
                             if self.is_direct else self.az.intent)

    @classmethod
    @async_getter_lock
    async def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_mxid(mxid))
        if portal:
            await portal.postinit()
            return portal

        return None

    @classmethod
    @async_getter_lock
    async def get_by_fbid(cls, fbid: int, *, fb_receiver: int = 0, create: bool = True,
                          fb_type: Optional[ThreadType] = None) -> Optional['Portal']:
        if fb_type:
            fb_receiver = fb_receiver if fb_type == ThreadType.USER else 0
        fbid_full = (fbid, fb_receiver)
        try:
            return cls.by_fbid[fbid_full]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_fbid(fbid, fb_receiver))
        if portal:
            await portal.postinit()
            return portal

        if fb_type and create:
            portal = cls(fbid=fbid, fb_receiver=fb_receiver, fb_type=fb_type)
            await portal.insert()
            await portal.postinit()
            return portal

        return None

    @classmethod
    async def get_all_by_receiver(cls, fb_receiver: int) -> AsyncGenerator['Portal', None]:
        portals = await super().get_all_by_receiver(fb_receiver)
        portal: Portal
        for portal in portals:
            try:
                yield cls.by_fbid[(portal.fbid, portal.fb_receiver)]
            except KeyError:
                await portal.postinit()
                yield portal

    @classmethod
    async def all(cls) -> AsyncGenerator['Portal', None]:
        portals = await super().all()
        portal: Portal
        for portal in portals:
            try:
                yield cls.by_fbid[(portal.fbid, portal.fb_receiver)]
            except KeyError:
                await portal.postinit()
                yield portal

    @classmethod
    def get_by_thread(cls, key: Union[graphql.ThreadKey, mqtt.ThreadKey],
                      fb_receiver: Optional[int] = None, create: bool = True
                      ) -> Awaitable['Portal']:
        return cls.get_by_fbid(key.id, fb_receiver=fb_receiver, create=create,
                               fb_type=ThreadType.from_thread_key(key))

    # endregion
