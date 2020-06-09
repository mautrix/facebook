# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import (Dict, Deque, Optional, Tuple, Union, Set, Iterator, List, Callable, Awaitable,
                    TYPE_CHECKING)
from tempfile import NamedTemporaryFile
from datetime import datetime, timezone
from collections import deque
import asyncio
import shutil

from yarl import URL
import aiohttp
import magic

import fbchat
from mautrix.types import (RoomID, EventType, ContentURI, MessageEventContent, EventID,
                           ImageInfo, MessageType, LocationMessageEventContent, LocationInfo,
                           ThumbnailInfo, FileInfo, AudioInfo, Format, RelatesTo, RelationType,
                           TextMessageEventContent, MediaMessageEventContent, Membership,
                           EncryptedFile)
from mautrix.appservice import IntentAPI
from mautrix.errors import MForbidden, IntentError, MatrixError
from mautrix.bridge import BasePortal, NotificationDisabler
from mautrix.util.simple_lock import SimpleLock

from .formatter import facebook_to_matrix, matrix_to_facebook
from .config import Config
from .db import (Portal as DBPortal, Message as DBMessage, Reaction as DBReaction,
                 UserPortal as DBUserPortal, ThreadType)
from . import puppet as p, user as u

if TYPE_CHECKING:
    from .context import Context
    from .matrix import MatrixHandler

try:
    from PIL import Image

    convert_cmd = shutil.which("convert")
except ImportError:
    Image = convert_cmd = None

try:
    from nio.crypto import decrypt_attachment, encrypt_attachment
except ImportError:
    decrypt_attachment = encrypt_attachment = None

config: Config

ThreadClass = Union[fbchat.UserData, fbchat.GroupData, fbchat.PageData]


class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


class Portal(BasePortal):
    invite_own_puppet_to_pm: bool = False
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_fbid: Dict[Tuple[str, str], 'Portal'] = {}
    matrix: 'MatrixHandler'

    fbid: str
    fb_receiver: str
    fb_type: ThreadType
    mxid: Optional[RoomID]
    encrypted: bool

    name: str
    photo_id: str

    _db_instance: DBPortal

    _main_intent: Optional[IntentAPI]
    _create_room_lock: asyncio.Lock
    _last_bridged_mxid: Optional[EventID]
    _dedup: Deque[str]
    _avatar_uri: Optional[ContentURI]
    _send_locks: Dict[str, asyncio.Lock]
    _noop_lock: FakeLock = FakeLock()
    _typing: Set['u.User']
    backfill_lock: SimpleLock

    def __init__(self, fbid: str, fb_receiver: str, fb_type: ThreadType,
                 mxid: Optional[RoomID] = None, encrypted: bool = False, name: str = "",
                 photo_id: str = "", db_instance: Optional[DBPortal] = None) -> None:
        self.fbid = fbid
        self.fb_receiver = fb_receiver
        self.fb_type = fb_type
        self.mxid = mxid
        self.encrypted = encrypted

        self.name = name
        self.photo_id = photo_id

        self.log = self.log.getChild(self.fbid_log)

        self._db_instance = db_instance

        self._main_intent = None
        self._create_room_lock = asyncio.Lock()
        self._last_bridged_mxid = None
        self._dedup = deque(maxlen=100)
        self._avatar_uri = None
        self._send_locks = {}
        self._typing = set()

        self.backfill_lock = SimpleLock("Waiting for backfilling to finish before handling %s",
                                        log=self.log, loop=self.loop)

        self.by_fbid[self.fbid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self

    # region DB conversion

    @property
    def db_instance(self) -> DBPortal:
        if not self._db_instance:
            self._db_instance = DBPortal(fbid=self.fbid, fb_receiver=self.fb_receiver,
                                         fb_type=self.fb_type, mxid=self.mxid, name=self.name,
                                         encrypted=self.encrypted, photo_id=self.photo_id)
        return self._db_instance

    @classmethod
    def from_db(cls, db_portal: DBPortal) -> 'Portal':
        return Portal(fbid=db_portal.fbid, fb_receiver=db_portal.fb_receiver,
                      fb_type=db_portal.fb_type, mxid=db_portal.mxid, name=db_portal.name,
                      encrypted=db_portal.encrypted, photo_id=db_portal.photo_id,
                      db_instance=db_portal)

    def save(self) -> None:
        self.db_instance.edit(mxid=self.mxid, name=self.name, photo_id=self.photo_id,
                              encrypted=self.encrypted)

    def delete(self) -> None:
        self.by_fbid.pop(self.fbid_full, None)
        self.by_mxid.pop(self.mxid, None)
        if self._db_instance:
            self._db_instance.delete()

    # endregion
    # region Properties

    @property
    def fbid_full(self) -> Tuple[str, str]:
        return self.fbid, self.fb_receiver

    @property
    def fbid_log(self) -> str:
        if self.is_direct:
            return f"{self.fbid}<->{self.fb_receiver}"
        return self.fbid

    def thread_for(self, user: 'u.User') -> Union[fbchat.User, fbchat.Group, fbchat.Page]:
        if self.fb_type == ThreadType.USER:
            return fbchat.User(session=user.session, id=self.fbid)
        elif self.fb_type == ThreadType.GROUP:
            return fbchat.Group(session=user.session, id=self.fbid)
        elif self.fb_type == ThreadType.PAGE:
            return fbchat.Page(session=user.session, id=self.fbid)
        else:
            raise ValueError("Unsupported thread type")

    @property
    def is_direct(self) -> bool:
        return self.fb_type == ThreadType.USER

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            self._main_intent = (p.Puppet.get_by_fbid(self.fbid).default_mxid_intent
                                 if self.is_direct else self.az.intent)

        return self._main_intent

    # endregion
    # region Chat info updating

    async def update_info(self, source: Optional['u.User'] = None,
                          info: Optional[ThreadClass] = None) -> Optional[ThreadClass]:
        if not info:
            self.log.debug("Called update_info with no info, fetching thread info...")
            info = await source.client.fetch_thread_info([self.fbid]).__anext__()
        self.log.trace("Thread info for %s: %s", self.fbid, info)
        if not isinstance(info, (fbchat.UserData, fbchat.GroupData, fbchat.PageData)):
            self.log.warning("Got weird info for %s of type %s, cancelling update",
                             self.fbid, type(info))
            return None
        changed = any(await asyncio.gather(self._update_name(info.name),
                                           self._update_photo(info.photo),
                                           self._update_participants(source, info),
                                           loop=self.loop))
        if changed:
            self.save()
        return info

    @staticmethod
    def _get_photo_id(photo: Optional[Union[fbchat.Image, str]]) -> Optional[str]:
        if not photo:
            return None
        elif isinstance(photo, fbchat.Image):
            photo = photo.url
        path = URL(photo).path
        return path[path.rfind("/") + 1:]

    @staticmethod
    async def _reupload_fb_file(url: str, intent: IntentAPI, filename: Optional[str] = None,
                                convert: Optional[Callable[[bytes], Awaitable[bytes]]] = None,
                                encrypt: bool = False
                                ) -> Tuple[ContentURI, str, int, Optional[EncryptedFile]]:
        if not url:
            raise ValueError('URL not provided')
        async with aiohttp.ClientSession() as session:
            resp = await session.get(url)
            data = await resp.read()
        if convert:
            data = await convert(data)
        mime = magic.from_buffer(data, mime=True)
        upload_mime_type = mime
        decryption_info = None
        if encrypt and encrypt_attachment:
            data, decryption_info_dict = encrypt_attachment(data)
            decryption_info = EncryptedFile.deserialize(decryption_info_dict)
            upload_mime_type = "application/octet-stream"
        url = await intent.upload_media(data, mime_type=upload_mime_type, filename=filename)
        if decryption_info:
            decryption_info.url = url
        return url, mime, len(data), decryption_info

    @staticmethod
    async def _convert_fb_sticker(data: bytes, frames_per_row: int, frames_per_col: int
                                  ) -> Tuple[bytes, int, int]:
        ntf = NamedTemporaryFile
        with ntf(suffix=".png") as input_file, ntf(suffix=".gif") as output_file:
            input_file.write(data)
            with Image.open(input_file) as img:
                width, height = img.size
            width /= frames_per_row
            height /= frames_per_col
            proc = await asyncio.create_subprocess_exec(convert_cmd,
                                                        "-dispose", "Background",
                                                        input_file.name,
                                                        "-crop", f"{width}x{height}",
                                                        "+adjoin", "+repage", "-adjoin",
                                                        "-loop", "0",
                                                        output_file.name)
            await proc.wait()
            return output_file.read(), width, height

    async def _update_name(self, name: str) -> bool:
        if not name:
            self.log.warning("Got empty name in _update_name call")
            return
        if self.name != name:
            self.log.trace("Updating name %s -> %s", self.name, name)
            self.name = name
            if self.mxid and (self.encrypted or not self.is_direct):
                await self.main_intent.set_room_name(self.mxid, self.name)
            return True
        return False

    async def _update_photo(self, photo: fbchat.Image) -> bool:
        if self.is_direct and not self.encrypted:
            return False
        photo_id = self._get_photo_id(photo)
        if self.photo_id != photo_id:
            self.photo_id = photo_id
            if photo:
                self._avatar_uri, *_ = await self._reupload_fb_file(photo.url, self.main_intent)
            else:
                self._avatar_uri = ContentURI("")
            if self.mxid:
                await self.main_intent.set_room_avatar(self.mxid, self._avatar_uri)
            return True
        return False

    async def _update_participants(self, source: 'u.User', info: ThreadClass) -> None:
        if self.is_direct:
            await p.Puppet.get_by_fbid(info.id).update_info(source=source, info=info)
            return
        elif not self.mxid:
            return
        # TODO maybe change this back to happen simultaneously
        async for user in source.client.fetch_thread_info([user.id for user in info.participants]):
            if not isinstance(user, fbchat.UserData):
                # TODO log
                continue
            puppet = p.Puppet.get_by_fbid(user.id)
            await puppet.update_info(source, user)
            await puppet.intent_for(self).ensure_joined(self.mxid)

    # endregion
    # region Matrix room creation

    async def update_matrix_room(self, source: 'u.User', info: Optional[ThreadClass] = None
                                 ) -> None:
        try:
            await self._update_matrix_room(source, info)
        except Exception:
            self.log.exception("Failed to update portal")

    async def _update_matrix_room(self, source: 'u.User',
                                  info: Optional[ThreadClass] = None) -> None:
        await self.main_intent.invite_user(self.mxid, source.mxid, check_cache=True)
        puppet = p.Puppet.get_by_custom_mxid(source.mxid)
        if puppet:
            await puppet.intent.ensure_joined(self.mxid)

        await self.update_info(source, info)

        up = DBUserPortal.get(source.fbid, self.fbid, self.fb_receiver)
        if not up:
            in_community = await source._community_helper.add_room(source._community_id, self.mxid)
            DBUserPortal(user=source.fbid, portal=self.fbid, portal_receiver=self.fb_receiver,
                         in_community=in_community).insert()
        elif not up.in_community:
            in_community = await source._community_helper.add_room(source._community_id, self.mxid)
            up.edit(in_community=in_community)

    async def create_matrix_room(self, source: 'u.User', info: Optional[ThreadClass] = None
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

    async def _create_matrix_room(self, source: 'u.User', info: Optional[ThreadClass] = None
                                  ) -> Optional[RoomID]:
        if self.mxid:
            await self._update_matrix_room(source, info)
            return self.mxid

        info = await self.update_info(source=source, info=info)
        if not info:
            return None
        self.log.debug(f"Creating Matrix room")
        name: Optional[str] = None
        bridge_info = {
            "bridgebot": self.az.bot_mxid,
            "creator": self.main_intent.mxid,
            "protocol": {
                "id": "facebook",
                "displayname": "Facebook Messenger",
                "avatar_url": config["appservice.bot_avatar"],
            },
            "channel": {
                "id": self.fbid
            }
        }
        initial_state = [{
            "type": "m.bridge",
            "state_key": f"net.maunium.facebook://facebook/{self.fbid}",
            "content": bridge_info
        }, {
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            "type": "uk.half-shot.bridge",
            "state_key": f"net.maunium.facebook://facebook/{self.fbid}",
            "content": bridge_info
        }]
        invites = [source.mxid]
        if config["bridge.encryption.default"] and self.matrix.e2ee:
            self.encrypted = True
            initial_state.append({
                "type": "m.room.encryption",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            })
            if self.is_direct:
                invites.append(self.az.bot_mxid)
        if self.encrypted or not self.is_direct:
            name = self.name
            initial_state.append({"type": str(EventType.ROOM_AVATAR),
                                  "content": {"avatar_url": self._avatar_uri}})
        if config["appservice.community_id"]:
            initial_state.append({
                "type": "m.room.related_groups",
                "content": {"groups": [config["appservice.community_id"]]},
            })

        # We lock backfill lock here so any messages that come between the room being created
        # and the initial backfill finishing wouldn't be bridged before the backfill messages.
        with self.backfill_lock:
            self.mxid = await self.main_intent.create_room(name=name, is_direct=self.is_direct,
                                                           initial_state=initial_state,
                                                           invitees=invites)
            if not self.mxid:
                raise Exception("Failed to create room: no mxid returned")

            if self.encrypted and self.matrix.e2ee:
                members = [self.main_intent.mxid]
                if self.is_direct:
                    # This isn't very accurate, but let's do it anyway
                    members += [source.mxid]
                    try:
                        await self.az.intent.join_room_by_id(self.mxid)
                        members += [self.az.intent.mxid]
                    except Exception:
                        self.log.warning("Failed to add bridge bot "
                                         f"to new private chat {self.mxid}")
                await self.matrix.e2ee.add_room(self.mxid, members=members, encrypted=True)

            self.save()
            self.log.debug(f"Matrix room created: {self.mxid}")
            self.by_mxid[self.mxid] = self
            if not self.is_direct:
                await self._update_participants(source, info)
            else:
                puppet = p.Puppet.get_by_custom_mxid(source.mxid)
                if puppet:
                    await puppet.intent.ensure_joined(self.mxid)

            in_community = await source._community_helper.add_room(source._community_id, self.mxid)
            DBUserPortal(user=source.fbid, portal=self.fbid, portal_receiver=self.fb_receiver,
                         in_community=in_community).upsert()

            try:
                await self.backfill(source, is_initial=True)
            except Exception:
                self.log.exception("Failed to backfill new portal")

        return self.mxid

    # endregion
    # region Matrix room cleanup

    @staticmethod
    async def cleanup_room(intent: IntentAPI, room_id: RoomID, message: str = "Portal deleted",
                           puppets_only: bool = False) -> None:
        try:
            members = await intent.get_room_members(room_id)
        except MatrixError:
            members = []
        for user_id in members:
            puppet = p.Puppet.get_by_mxid(user_id, create=False)
            if user_id != intent.mxid and (not puppets_only or puppet):
                try:
                    if puppet:
                        await puppet.intent.leave_room(room_id)
                    else:
                        await intent.kick_user(room_id, user_id, message)
                except MatrixError:
                    pass
        try:
            await intent.leave_room(room_id)
        except MatrixError:
            pass
        DBMessage.delete_all_by_mxid(room_id)

    async def unbridge(self) -> None:
        await self.cleanup_room(self.main_intent, self.mxid, "Room unbridged", puppets_only=True)
        self.delete()

    async def cleanup_and_delete(self) -> None:
        await self.cleanup_room(self.main_intent, self.mxid)
        self.delete()

    # endregion
    # region Matrix event handling

    def require_send_lock(self, user_id: str) -> asyncio.Lock:
        try:
            lock = self._send_locks[user_id]
        except KeyError:
            lock = asyncio.Lock()
            self._send_locks[user_id] = lock
        return lock

    def optional_send_lock(self, user_id: str) -> Union[asyncio.Lock, FakeLock]:
        try:
            return self._send_locks[user_id]
        except KeyError:
            pass
        return self._noop_lock

    async def _send_delivery_receipt(self, event_id: EventID) -> None:
        if event_id and config["bridge.delivery_receipts"]:
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
        try:
            await self._handle_matrix_message(sender, message, event_id)
        except fbchat.PleaseRefresh:
            self.log.debug(f"Got PleaseRefresh error while trying to bridge {event_id}")
            await sender.refresh()
            try:
                await self._handle_matrix_message(sender, message, event_id)
            except fbchat.FacebookError as e:
                self.log.exception(f"Got FacebookError while trying to bridge {event_id} "
                                   "after auto-refreshing")
                await self._send_bridge_error(e.message)
        except fbchat.FacebookError as e:
            self.log.exception(f"Got FacebookError while trying to bridge {event_id}")
            await self._send_bridge_error(e.message)

    async def _handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                     event_id: EventID) -> None:
        if ((message.get("net.maunium.facebook.puppet", False)
             and p.Puppet.get_by_custom_mxid(sender.mxid))):
            self.log.debug(f"Ignoring puppet-sent message by confirmed puppet user {sender.mxid}")
            return
        # TODO this probably isn't nice for bridging images, it really only needs to lock the
        #      actual message send call and dedup queue append.
        async with self.require_send_lock(sender.fbid):
            date = datetime.now(tz=timezone.utc)
            if message.msgtype == MessageType.TEXT or message.msgtype == MessageType.NOTICE:
                fbid = await self._handle_matrix_text(sender, message)
            elif message.msgtype == MessageType.IMAGE:
                fbid = await self._handle_matrix_image(sender, message)
            elif message.msgtype == MessageType.LOCATION:
                fbid = await self._handle_matrix_location(sender, message)
            else:
                self.log.warning(f"Unsupported msgtype {message.msgtype} in {event_id}")
                return
            if not fbid:
                return
            if isinstance(fbid, tuple) and len(fbid) > 0:
                fbid = fbid[0]
            self._dedup.appendleft(fbid)
            DBMessage(mxid=event_id, mx_room=self.mxid,
                      fbid=fbid, fb_chat=self.fbid, fb_receiver=self.fb_receiver,
                      index=0, date=date).insert()
            self._last_bridged_mxid = event_id
        await self._send_delivery_receipt(event_id)

    async def _handle_matrix_text(self, sender: 'u.User', message: TextMessageEventContent) -> str:
        return await self.thread_for(sender).send_text(**matrix_to_facebook(message, self.mxid))

    async def _handle_matrix_image(self, sender: 'u.User',
                                   message: MediaMessageEventContent) -> Optional[str]:
        if message.file and decrypt_attachment:
            data = await self.main_intent.download_media(message.file.url)
            data = decrypt_attachment(data, message.file.key.key,
                                      message.file.hashes.get("sha256"), message.file.iv)
        elif message.url:
            data = await self.main_intent.download_media(message.url)
        else:
            return None
        mime = message.info.mimetype or magic.from_buffer(data, mime=True)
        files = await sender.client.upload([(message.body, data, mime)])
        return await self.thread_for(sender).send_files(files)

    async def _handle_matrix_location(self, sender: 'u.User',
                                      message: LocationMessageEventContent) -> str:
        pass

    async def handle_matrix_redaction(self, sender: 'u.User', event_id: EventID,
                                      redaction_event_id: EventID) -> None:
        if not self.mxid:
            return

        message = DBMessage.get_by_mxid(event_id, self.mxid)
        if message:
            try:
                message.delete()
                await fbchat.Message(thread=self.thread_for(sender), id=message.fbid).unsend()
                await self._send_delivery_receipt(redaction_event_id)
            except Exception:
                self.log.exception("Unsend failed")
            return

        reaction = DBReaction.get_by_mxid(event_id, self.mxid)
        if reaction:
            try:
                reaction.delete()
                await fbchat.Message(thread=self.thread_for(sender),
                                     id=reaction.fb_msgid).react(None)
                await self._send_delivery_receipt(redaction_event_id)
            except Exception:
                self.log.exception("Removing reaction failed")

    async def handle_matrix_reaction(self, sender: 'u.User', event_id: EventID,
                                     reacting_to: EventID, reaction: str) -> None:
        async with self.require_send_lock(sender.fbid):
            message = DBMessage.get_by_mxid(reacting_to, self.mxid)
            if not message:
                self.log.debug(f"Ignoring reaction to unknown event {reacting_to}")
                return

            existing = DBReaction.get_by_fbid(message.fbid, self.fb_receiver, sender.fbid)
            if existing and existing.reaction == reaction:
                return

            # TODO normalize reaction emoji bytes and maybe pre-reject invalid emojis
            await fbchat.Message(thread=self.thread_for(sender), id=message.fbid).react(reaction)
            await self._upsert_reaction(existing, self.main_intent, event_id, message, sender,
                                        reaction)
        await self._send_delivery_receipt(event_id)

    async def handle_matrix_leave(self, user: 'u.User') -> None:
        if self.is_direct:
            self.log.info(f"{user.mxid} left private chat portal with {self.fbid}")
            if user.fbid == self.fb_receiver:
                self.log.info(f"{user.mxid} was the recipient of this portal. "
                              "Cleaning up and deleting...")
                await self.cleanup_and_delete()
        else:
            self.log.debug(f"{user.mxid} left portal to {self.fbid}")

    async def handle_matrix_typing(self, users: Set['u.User']) -> None:
        stopped_typing = [self.thread_for(user).stop_typing() for user in self._typing - users]
        started_typing = [self.thread_for(user).start_typing() for user in users - self._typing]
        self._typing = users
        await asyncio.gather(*stopped_typing, *started_typing, loop=self.loop)

    # endregion
    # region Facebook event handling

    async def _bridge_own_message_pm(self, source: 'u.User', sender: 'p.Puppet', mid: str,
                                     invite: bool = True) -> bool:
        if self.is_direct and sender.fbid == source.fbid and not sender.is_real_user:
            if self.invite_own_puppet_to_pm and invite:
                await self.main_intent.invite_user(self.mxid, sender.mxid)
            elif self.az.state_store.get_membership(self.mxid, sender.mxid) != Membership.JOIN:
                self.log.warning(f"Ignoring own {mid} in private chat because own puppet is not in"
                                 " room.")
                return False
        return True

    async def handle_facebook_message(self, source: 'u.User', sender: 'p.Puppet',
                                      message: fbchat.MessageData) -> None:
        if self.backfill_lock.locked and DBMessage.get_by_fbid(message.id, self.fb_receiver):
            self.log.trace("Not handling message %s, found duplicate in database", message.id)
            return
        async with self.optional_send_lock(sender.fbid):
            if message.id in self._dedup:
                await source.client.mark_as_delivered(message)
                return
            self._dedup.appendleft(message.id)
        if not self.mxid:
            mxid = await self.create_matrix_room(source)
            if not mxid:
                # Failed to create
                return
        if not await self._bridge_own_message_pm(source, sender, f"message {message.id}"):
            return
        intent = sender.intent_for(self)
        event_ids = []
        if message.sticker:
            event_ids = [await self._handle_facebook_sticker(
                intent, message.sticker, message.reply_to_id, message.created_at)]
        elif len(message.attachments) > 0:
            attach_ids = await asyncio.gather(
                *[self._handle_facebook_attachment(source.client, intent, attachment,
                                                   message.reply_to_id, message.created_at)
                  for attachment in message.attachments])
            event_ids += [attach_id for attach_id in attach_ids if attach_id]
        if not event_ids:
            if message.text or any(x for x in message.attachments
                                   if isinstance(x, fbchat.ShareAttachment)):
                event_ids = [await self._handle_facebook_text(intent, message)]
            else:
                self.log.warning(f"Unhandled Messenger message {message.id}")
                self.log.trace("Message %s content: %s", message.id, message)
                return
        DBMessage.bulk_create(fbid=message.id, fb_chat=self.fbid, fb_receiver=self.fb_receiver,
                              mx_room=self.mxid, date=message.created_at.astimezone(timezone.utc),
                              event_ids=[event_id for event_id in event_ids if event_id])
        await source.client.mark_as_delivered(message)
        if event_ids:
            self._last_bridged_mxid = event_ids[-1]
            await self._send_delivery_receipt(self._last_bridged_mxid)

    async def _add_facebook_reply(self, content: TextMessageEventContent, reply: str) -> None:
        if reply:
            message = DBMessage.get_by_fbid(reply, self.fb_receiver)
            if message:
                evt = await self.main_intent.get_event(message.mx_room, message.mxid)
                if evt:
                    if isinstance(evt.content, TextMessageEventContent):
                        evt.content.trim_reply_fallback()
                    content.set_reply(evt)

    def _get_facebook_reply(self, reply: str) -> Optional[RelatesTo]:
        if reply:
            message = DBMessage.get_by_fbid(reply, self.fb_receiver)
            if message:
                return RelatesTo(rel_type=RelationType.REFERENCE, event_id=message.mxid)
        return None

    async def _send_message(self, intent: IntentAPI, content: MessageEventContent,
                            event_type: EventType = EventType.ROOM_MESSAGE, **kwargs) -> EventID:
        if self.encrypted and self.matrix.e2ee:
            if intent.api.is_real_user:
                content[intent.api.real_user_content_key] = True
            event_type, content = await self.matrix.e2ee.encrypt(self.mxid, event_type, content)
        return await intent.send_message_event(self.mxid, event_type, content, **kwargs)

    async def _handle_facebook_text(self, intent: IntentAPI, message: fbchat.MessageData
                                    ) -> EventID:
        content = facebook_to_matrix(message)
        await self._add_facebook_reply(content, message.reply_to_id)
        return await self._send_message(intent, content, timestamp=message.created_at)

    async def _handle_facebook_sticker(self, intent: IntentAPI, sticker: fbchat.Sticker,
                                       reply_to: str, timestamp: datetime) -> EventID:
        width, height = sticker.image.width, sticker.image.height
        if sticker.is_animated and Image and convert_cmd:
            async def convert(data: bytes) -> bytes:
                nonlocal width, height
                data, width, height = await self._convert_fb_sticker(data, sticker.frames_per_row,
                                                                     sticker.frames_per_col)
                return data

            mxc, mime, size, decryption_info = await self._reupload_fb_file(
                sticker.large_sprite_image, intent, encrypt=self.encrypted, convert=convert)
        else:
            mxc, mime, size, decryption_info = await self._reupload_fb_file(
                sticker.image.url, intent, encrypt=self.encrypted)
        return await self._send_message(intent, event_type=EventType.STICKER,
                                        content=MediaMessageEventContent(
                                            url=mxc, file=decryption_info,
                                            msgtype=MessageType.STICKER, body=sticker.label or "",
                                            info=ImageInfo(width=width, size=size,
                                                           height=height, mimetype=mime),
                                            relates_to=self._get_facebook_reply(reply_to)),
                                        timestamp=timestamp)

    async def _handle_facebook_attachment(self, source: fbchat.Client, intent: IntentAPI,
                                          attachment: fbchat.Attachment, reply_to: str,
                                          timestamp: datetime) -> Optional[EventID]:
        if isinstance(attachment, fbchat.AudioAttachment):
            mxc, mime, size, decryption_info = await self._reupload_fb_file(
                attachment.url, intent, attachment.filename, encrypt=self.encrypted)
            event_id = await self._send_message(intent, MediaMessageEventContent(
                url=mxc, file=decryption_info, msgtype=MessageType.AUDIO, body=attachment.filename,
                info=AudioInfo(size=size, mimetype=mime, duration=attachment.duration.seconds),
                relates_to=self._get_facebook_reply(reply_to)), timestamp=timestamp)
        # elif isinstance(attachment, fbchat.VideoAttachment):
        # TODO
        elif isinstance(attachment, fbchat.FileAttachment):
            mxc, mime, size, decryption_info = await self._reupload_fb_file(
                attachment.url, intent, attachment.name, encrypt=self.encrypted)
            event_id = await self._send_message(intent, MediaMessageEventContent(
                url=mxc, file=decryption_info, msgtype=MessageType.FILE, body=attachment.name,
                info=FileInfo(size=size, mimetype=mime),
                relates_to=self._get_facebook_reply(reply_to)), timestamp=timestamp)
        elif isinstance(attachment, fbchat.ImageAttachment):
            mxc, mime, size, decryption_info = await self._reupload_fb_file(
                await source.fetch_image_url(attachment.id), intent, encrypt=self.encrypted)
            event_id = await self._send_message(intent, MediaMessageEventContent(
                url=mxc, file=decryption_info, msgtype=MessageType.IMAGE,
                body=f"image.{attachment.original_extension}",
                info=ImageInfo(size=size, mimetype=mime, width=attachment.width,
                               height=attachment.height),
                relates_to=self._get_facebook_reply(reply_to)), timestamp=timestamp)
        elif isinstance(attachment, fbchat.LocationAttachment):
            content = await self._convert_facebook_location(intent, attachment)
            content.relates_to = self._get_facebook_reply(reply_to)
            event_id = await self._send_message(intent, content, timestamp=timestamp)
        elif isinstance(attachment, fbchat.ShareAttachment):
            # These are handled in the text formatter
            return None
        else:
            self.log.warning(f"Unsupported attachment type {type(attachment)}")
            return None
        return event_id

    async def _convert_facebook_location(self, intent: IntentAPI,
                                         location: fbchat.LocationAttachment
                                         ) -> LocationMessageEventContent:
        long, lat = location.longitude, location.latitude
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = round(long, 5)
        rounded_lat = round(lat, 5)

        text = f"{rounded_lat}° {lat_char}, {rounded_long}° {long_char}"
        url = f"https://maps.google.com/?q={lat},{long}"

        thumbnail_url, mime, size, decryption_info = await self._reupload_fb_file(
            location.image_url, intent, encrypt=True)
        thumbnail_info = ThumbnailInfo(mimetype=mime, width=location.image_width,
                                       height=location.image_height, size=size)
        content = LocationMessageEventContent(
            body=f"{location.address}\nLocation: {text}\n{url}", geo_uri=f"geo:{lat},{long}",
            msgtype=MessageType.LOCATION, info=LocationInfo(thumbnail_url=thumbnail_url,
                                                            thumbnail_file=decryption_info,
                                                            thumbnail_info=thumbnail_info))
        # Some clients support formatted body in m.location, so add that as well.
        content["format"] = Format.HTML
        content["formatted_body"] = (f"<p>{location.address}</p>"
                                     f"<p>Location: <a href='{url}'>{text}</a></p")
        return content

    async def handle_facebook_unsend(self, source: 'u.User', sender: 'p.Puppet', message_id: str
                                     ) -> None:
        if not self.mxid:
            return
        for message in DBMessage.get_all_by_fbid(message_id, self.fb_receiver):
            try:
                await sender.intent_for(self).redact(message.mx_room, message.mxid)
            except MForbidden:
                await self.main_intent.redact(message.mx_room, message.mxid)
            message.delete()

    async def handle_facebook_seen(self, source: 'u.User', sender: 'p.Puppet') -> None:
        if not self.mxid or not self._last_bridged_mxid:
            return
        if not await self._bridge_own_message_pm(source, sender, "read receipt",
                                                 invite=False):
            return
        await sender.intent_for(self).mark_read(self.mxid, self._last_bridged_mxid)

    async def handle_facebook_typing(self, source: 'u.User', sender: 'p.Puppet') -> None:
        if not await self._bridge_own_message_pm(source, sender, "typing notification",
                                                 invite=False):
            return
        await sender.intent.set_typing(self.mxid, is_typing=True)

    async def handle_facebook_photo(self, source: 'u.User', sender: 'p.Puppet', new_photo_id: str,
                                    message_id: str) -> None:
        if not self.mxid or self.is_direct:
            return
        if message_id in self._dedup:
            return
        self._dedup.appendleft(message_id)
        # When we fetch thread info manually, we only get the URL instead of the ID,
        # so we can't use the actual ID here either.
        # self.photo_id = new_photo_id
        photo_url = await source.client.fetch_image_url(new_photo_id)
        photo_id = self._get_photo_id(photo_url)
        if self.photo_id == photo_id:
            return
        self.photo_id = photo_id
        self._avatar_uri, *_ = await self._reupload_fb_file(photo_url, sender.intent)
        try:
            event_id = await sender.intent.set_room_avatar(self.mxid, self._avatar_uri)
        except IntentError:
            event_id = await self.main_intent.set_room_avatar(self.mxid, self._avatar_uri)
        DBMessage(mxid=event_id, mx_room=self.mxid, index=0, date=None,
                  fbid=message_id, fb_chat=self.fbid, fb_receiver=self.fb_receiver).insert()

    async def handle_facebook_name(self, source: 'u.User', sender: 'p.Puppet', new_name: str,
                                   message_id: str) -> None:
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
        DBMessage(mxid=event_id, mx_room=self.mxid, index=0, date=None,
                  fbid=message_id, fb_chat=self.fbid, fb_receiver=self.fb_receiver).insert()

    async def handle_facebook_reaction_add(self, source: 'u.User', sender: 'p.Puppet',
                                           message_id: str, reaction: str) -> None:
        dedup_id = f"react_{message_id}_{sender}_{reaction}"
        async with self.optional_send_lock(sender.fbid):
            if dedup_id in self._dedup:
                return
            self._dedup.appendleft(dedup_id)

        existing = DBReaction.get_by_fbid(message_id, self.fb_receiver, sender.fbid)
        if existing and existing.reaction == reaction:
            return

        if not await self._bridge_own_message_pm(source, sender, f"reaction to {message_id}"):
            return

        intent = sender.intent_for(self)

        message = DBMessage.get_by_fbid(message_id, self.fb_receiver)
        if not message:
            self.log.debug(f"Ignoring reaction to unknown message {message_id}")
            return

        mxid = await intent.react(message.mx_room, message.mxid, reaction)
        self.log.debug(f"Reacted to {message.mxid}, got {mxid}")

        await self._upsert_reaction(existing, intent, mxid, message, sender, reaction)

    async def _upsert_reaction(self, existing: DBReaction, intent: IntentAPI, mxid: EventID,
                               message: DBMessage, sender: Union['u.User', 'p.Puppet'],
                               reaction: str) -> None:
        if existing:
            self.log.debug(f"_upsert_reaction redacting {existing.mxid} and inserting {mxid}"
                           f" (message: {message.mxid})")
            await intent.redact(existing.mx_room, existing.mxid)
            existing.edit(reaction=reaction, mxid=mxid, mx_room=message.mx_room)
        else:
            self.log.debug(f"_upsert_reaction inserting {mxid} (message: {message.mxid})")
            DBReaction(mxid=mxid, mx_room=message.mx_room, fb_msgid=message.fbid,
                       fb_receiver=self.fb_receiver, fb_sender=sender.fbid,
                       reaction=reaction).insert()

    async def handle_facebook_reaction_remove(self, source: 'u.User', sender: 'p.Puppet',
                                              message_id: str) -> None:
        if not self.mxid:
            return
        reaction = DBReaction.get_by_fbid(message_id, self.fb_receiver, sender.fbid)
        if reaction:
            try:
                await sender.intent_for(self).redact(reaction.mx_room, reaction.mxid)
            except MForbidden:
                await self.main_intent.redact(reaction.mx_room, reaction.mxid)
            reaction.delete()

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

    async def backfill(self, source: 'u.User', is_initial: bool,
                       last_active: Optional[datetime] = None) -> None:
        limit = (config["bridge.backfill.initial_limit"] if is_initial
                 else config["bridge.backfill.missed_limit"])
        if limit == 0:
            return
        elif limit < 0:
            limit = None
        most_recent = DBMessage.get_most_recent(self.fbid, self.fb_receiver)
        if most_recent and is_initial:
            self.log.debug("Not backfilling %s: already bridged messages found", self.fbid_log)
        elif not most_recent and not is_initial:
            self.log.debug("Not backfilling %s: no most recent message found", self.fbid_log)
        elif last_active and most_recent.date >= last_active:
            self.log.debug("Not backfilling %s: last activity is equal to most recent bridged "
                           "message (%s >= %s)", self.fbid_log, most_recent.date, last_active)
        else:
            with self.backfill_lock:
                await self._backfill(source, limit, most_recent.date if most_recent else None)

    async def _backfill(self, source: 'u.User', limit: int, limit_date: datetime) -> None:
        self.log.debug("Backfilling history through %s", source.mxid)
        thread = self.thread_for(source)
        messages = []
        self.log.debug("Fetching up to %d messages through %s", limit, source.fbid)
        async for message in thread.fetch_messages(limit):
            if limit_date and message.created_at < limit_date:
                self.log.debug("Stopping backfilling at %s as message is older than newest bridged"
                               " message (%s < %s)", message.id, message.created_at, limit_date)
                break
            messages.append(message)
        if not messages:
            self.log.debug("Didn't get any messages from server")
            return
        self.log.debug("Got %d messages from server", len(messages))
        backfill_leave = set()
        if config["bridge.backfill.invite_own_puppet"]:
            self.log.debug("Adding %s's default puppet to room for backfilling", source.mxid)
            sender = p.Puppet.get_by_fbid(source.fbid)
            await self.main_intent.invite_user(self.mxid, sender.default_mxid)
            await sender.default_mxid_intent.join_room_by_id(self.mxid)
            backfill_leave.add(sender.default_mxid_intent)
        async with NotificationDisabler(self.mxid, source):
            for message in reversed(messages):
                puppet = p.Puppet.get_by_fbid(message.author)
                await self.handle_facebook_message(source, puppet, message)
        for intent in backfill_leave:
            self.log.trace("Leaving room with %s post-backfill", intent.mxid)
            await intent.leave_room(self.mxid)
        self.log.info("Backfilled %d messages through %s", len(messages), source.mxid)

    # region Getters

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        db_portal = DBPortal.get_by_mxid(mxid)
        if db_portal:
            return cls.from_db(db_portal)

        return None

    @classmethod
    def get_by_fbid(cls, fbid: str, fb_receiver: Optional[str] = None,
                    fb_type: Optional[ThreadType] = None) -> Optional['Portal']:
        if fb_type:
            fb_receiver = fb_receiver if fb_type == ThreadType.USER else fbid
        else:
            fb_receiver = fb_receiver or fbid
        fbid_full = (fbid, fb_receiver)
        try:
            return cls.by_fbid[fbid_full]
        except KeyError:
            pass

        db_portal = DBPortal.get_by_fbid(fbid, fb_receiver)
        if db_portal:
            return cls.from_db(db_portal)

        if fb_type:
            portal = cls(fbid=fbid, fb_receiver=fb_receiver, fb_type=fb_type)
            portal.db_instance.insert()
            return portal

        return None

    @classmethod
    def get_all_by_receiver(cls, fb_receiver: str) -> Iterator['Portal']:
        for db_portal in DBPortal.get_all_by_receiver(fb_receiver):
            try:
                yield cls.by_fbid[(db_portal.fbid, db_portal.fb_receiver)]
            except KeyError:
                yield cls.from_db(db_portal)

    @classmethod
    def get_by_thread(cls, thread: fbchat.ThreadABC, fb_receiver: Optional[str] = None
                      ) -> 'Portal':
        return cls.get_by_fbid(thread.id, fb_receiver, ThreadType.from_thread(thread))

    # endregion


def init(context: 'Context') -> None:
    global config
    Portal.az, config, Portal.loop = context.core
    Portal.matrix = context.mx
    Portal.invite_own_puppet_to_pm = config["bridge.invite_own_puppet_to_pm"]
    NotificationDisabler.puppet_cls = p.Puppet
    NotificationDisabler.config_enabled = config["bridge.backfill.disable_notifications"]
