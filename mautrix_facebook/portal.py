# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Dict, Deque, Optional, Tuple, Union, Set, Iterator, TYPE_CHECKING
from collections import deque
import asyncio
import logging

from yarl import URL
import aiohttp
import magic

from fbchat.models import (ThreadType, Thread, User as FBUser, Group as FBGroup, Page as FBPage,
                           Message as FBMessage, Sticker as FBSticker, AudioAttachment,
                           VideoAttachment, FileAttachment, ImageAttachment, LocationAttachment,
                           ShareAttachment, TypingStatus, MessageReaction)
from mautrix.types import (RoomID, EventType, ContentURI, MessageEventContent, EventID,
                           ImageInfo, MessageType, LocationMessageEventContent, LocationInfo,
                           ThumbnailInfo, FileInfo, AudioInfo, VideoInfo, Format, RelatesTo,
                           TextMessageEventContent, MediaMessageEventContent, Membership,
                           RelationType)
from mautrix.appservice import AppService, IntentAPI
from mautrix.errors import MForbidden, IntentError, MatrixError

from .formatter import facebook_to_matrix, matrix_to_facebook
from .config import Config
from .db import Portal as DBPortal, Message as DBMessage, Reaction as DBReaction
from . import puppet as p, user as u

if TYPE_CHECKING:
    from .context import Context

config: Config

ThreadClass = Union[FBUser, FBGroup, FBPage]
AttachmentClass = Union[AudioAttachment, VideoAttachment, FileAttachment, ImageAttachment,
                        LocationAttachment, ShareAttachment]


class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


class Portal:
    az: AppService
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.portal")
    invite_own_puppet_to_pm: bool = False
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_fbid: Dict[Tuple[str, str], 'Portal'] = {}

    fbid: str
    fb_receiver: str
    fb_type: ThreadType
    mxid: Optional[RoomID]

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

    def __init__(self, fbid: str, fb_receiver: str, fb_type: ThreadType,
                 mxid: Optional[RoomID] = None, name: str = "", photo_id: str = "",
                 db_instance: Optional[DBPortal] = None) -> None:
        self.fbid = fbid
        self.fb_receiver = fb_receiver
        self.fb_type = fb_type
        self.mxid = mxid

        self.name = name
        self.photo_id = photo_id

        self._db_instance = db_instance

        self._main_intent = None
        self._create_room_lock = asyncio.Lock()
        self._last_bridged_mxid = None
        self._dedup = deque(maxlen=100)
        self._avatar_uri = None
        self._send_locks = {}
        self._typing = set()

        self.log = self.log.getChild(self.fbid_log)

        self.by_fbid[self.fbid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self

    # region DB conversion

    @property
    def db_instance(self) -> DBPortal:
        if not self._db_instance:
            self._db_instance = DBPortal(fbid=self.fbid, fb_receiver=self.fb_receiver,
                                         fb_type=self.fb_type, mxid=self.mxid, name=self.name,
                                         photo_id=self.photo_id)
        return self._db_instance

    @classmethod
    def from_db(cls, db_portal: DBPortal) -> 'Portal':
        return Portal(fbid=db_portal.fbid, fb_receiver=db_portal.fb_receiver,
                      fb_type=db_portal.fb_type, mxid=db_portal.mxid, name=db_portal.name,
                      photo_id=db_portal.photo_id, db_instance=db_portal)

    def save(self) -> None:
        self.db_instance.edit(mxid=self.mxid, name=self.name, photo_id=self.photo_id)

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

    @property
    def is_direct(self) -> bool:
        return self.fb_type == ThreadType.USER

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            self._main_intent = (p.Puppet.get_by_fbid(self.fbid).default_mxid_intent
                                 if self.is_direct
                                 else self.az.intent)

        return self._main_intent

    # endregion
    # region Chat info updating

    async def update_info(self, source: Optional['u.User'] = None,
                          info: Optional[ThreadClass] = None) -> ThreadClass:
        if not info:
            info = (await source.fetchThreadInfo(self.fbid))[self.fbid]
        changed = any(await asyncio.gather(self._update_name(info.name),
                                           self._update_photo(info.photo),
                                           self._update_participants(source, info),
                                           loop=self.loop))
        if changed:
            self.save()
        return info

    @staticmethod
    def _get_photo_id(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        path = URL(url).path
        return path[path.rfind("/") + 1:]

    @staticmethod
    async def _reupload_fb_photo(url: str, intent: IntentAPI, filename: Optional[str] = None
                                 ) -> Tuple[ContentURI, str, int]:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(url)
            data = await resp.read()
        mime = magic.from_buffer(data, mime=True)
        return await intent.upload_media(data, mime_type=mime, filename=filename), mime, len(data)

    async def _update_name(self, name: str) -> bool:
        if self.name != name:
            self.name = name
            if self.mxid and not self.is_direct:
                await self.main_intent.set_room_name(self.mxid, self.name)
            return True
        return False

    async def _update_photo(self, photo_url: str) -> bool:
        if self.is_direct:
            return False
        photo_id = self._get_photo_id(photo_url)
        if self.photo_id != photo_id:
            self.photo_id = photo_id
            if photo_url:
                self._avatar_uri, _, _ = await self._reupload_fb_photo(photo_url, self.main_intent)
            else:
                self._avatar_uri = ContentURI("")
            if self.mxid:
                await self.main_intent.set_room_avatar(self.mxid, self._avatar_uri)
            return True
        return False

    async def _update_participants(self, source: 'u.User', info: ThreadClass) -> None:
        if self.is_direct:
            await p.Puppet.get_by_fbid(info.uid).update_info(source=source, info=info)
            return
        elif not self.mxid:
            return
        users = await source.fetchAllUsersFromThreads([info])
        puppets = {user: p.Puppet.get_by_fbid(user.uid) for user in users}
        await asyncio.gather(*[puppet.update_info(source=source, info=user)
                               for user, puppet in puppets.items()])
        await asyncio.gather(*[puppet.intent_for(self).ensure_joined(self.mxid)
                               for puppet in puppets.values()])

    # endregion
    # region Matrix room creation

    async def _update_matrix_room(self, source: 'u.User',
                                  info: Optional[ThreadClass] = None) -> None:
        await self.main_intent.invite_user(self.mxid, source.mxid)
        puppet = p.Puppet.get_by_custom_mxid(source.mxid)
        if puppet:
            await puppet.intent.ensure_joined(self.mxid)

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
                                  ) -> RoomID:
        if self.mxid:
            await self._update_matrix_room(source, info)
            return self.mxid

        info = await self.update_info(source=source, info=info)
        self.log.debug(f"Creating Matrix room")
        name: Optional[str] = None
        initial_state = []
        if not self.is_direct:
            name = self.name
            initial_state.append({"type": str(EventType.ROOM_AVATAR),
                                  "content": {"avatar_url": self._avatar_uri}})
        self.mxid = await self.main_intent.create_room(name=name, is_direct=self.is_direct,
                                                       initial_state=initial_state,
                                                       invitees=[source.mxid])
        self.save()
        self.log.debug(f"Matrix room created: {self.mxid}")
        if not self.mxid:
            raise Exception("Failed to create room: no mxid required")
        self.by_mxid[self.mxid] = self
        if not self.is_direct:
            await self._update_participants(source, info)
        else:
            puppet = p.Puppet.get_by_custom_mxid(source.mxid)
            if puppet:
                await puppet.intent.ensure_joined(self.mxid)
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

    async def handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                    event_id: EventID) -> None:
        puppet = p.Puppet.get_by_custom_mxid(sender.mxid)
        if puppet and message.get("net.maunium.facebook.puppet", False):
            self.log.debug(f"Ignoring puppet-sent message by confirmed puppet user {sender.mxid}")
            return
        # TODO this probably isn't nice for bridging images, it really only needs to lock the
        #      actual message send call and dedup queue append.
        async with self.require_send_lock(sender.uid):
            if message.msgtype == MessageType.TEXT or message.msgtype == MessageType.NOTICE:
                fbid = await self._handle_matrix_text(sender, message)
            elif message.msgtype == MessageType.IMAGE:
                fbid = await self._handle_matrix_image(sender, message)
            elif message.msgtype == MessageType.LOCATION:
                fbid = await self._handle_matrix_location(sender, message)
            else:
                self.log.warn(f"Unsupported msgtype in {message}")
                return
            if not fbid:
                return
            self._dedup.appendleft(fbid)
            DBMessage(mxid=event_id, mx_room=self.mxid,
                      fbid=fbid, fb_receiver=self.fb_receiver,
                      index=0).insert()
            self._last_bridged_mxid = event_id

    async def _handle_matrix_text(self, sender: 'u.User', message: TextMessageEventContent) -> str:
        return await sender.send(matrix_to_facebook(message, self.mxid), self.fbid, self.fb_type)

    async def _handle_matrix_image(self, sender: 'u.User',
                                   message: MediaMessageEventContent) -> str:
        data = await self.main_intent.download_media(message.url)
        mime = message.info.mimetype or magic.from_buffer(data, mime=True)
        files = await sender._upload([(message.body, data, mime)])
        return await sender._sendFiles(files, thread_id=self.fbid, thread_type=self.fb_type)

    async def _handle_matrix_location(self, sender: 'u.User',
                                      message: LocationMessageEventContent) -> str:
        pass

    async def handle_matrix_redaction(self, sender: 'u.User', event_id: EventID) -> None:
        if not self.mxid:
            return

        message = DBMessage.get_by_mxid(event_id, self.mxid)
        if message:
            try:
                message.delete()
                await sender.unsend(message.fbid)
            except Exception:
                self.log.exception("Unsend failed")
            return

        reaction = DBReaction.get_by_mxid(event_id, self.mxid)
        if reaction:
            try:
                reaction.delete()
                await sender.reactToMessage(reaction.fb_msgid, reaction=None)
            except Exception:
                self.log.exception("Removing reaction failed")

    _matrix_to_facebook_reaction = {
        "❤": MessageReaction.HEART,
        "❤️": MessageReaction.HEART,
        "😍": MessageReaction.LOVE,
        "😆": MessageReaction.SMILE,
        "😮": MessageReaction.WOW,
        "😢": MessageReaction.SAD,
        "😠": MessageReaction.ANGRY,
        "👍": MessageReaction.YES,
        "👎": MessageReaction.NO
    }

    async def handle_matrix_reaction(self, sender: 'u.User', event_id: EventID,
                                     reacting_to: EventID, raw_reaction: str) -> None:
        async with self.require_send_lock(sender.uid):
            try:
                reaction = self._matrix_to_facebook_reaction[raw_reaction]
            except KeyError:
                return

            message = DBMessage.get_by_mxid(reacting_to, self.mxid)
            if not message:
                self.log.debug(f"Ignoring reaction to unknown event {reacting_to}")
                return

            existing = DBReaction.get_by_fbid(message.fbid, self.fb_receiver, sender.uid)
            if existing and existing.reaction == reaction.value:
                return

            await sender.reactToMessage(message.fbid, reaction)
            await self._upsert_reaction(existing, self.main_intent, event_id, message, sender,
                                        reaction.value)

    async def handle_matrix_leave(self, user: 'u.User') -> None:
        if self.is_direct:
            self.log.info(f"{user.mxid} left private chat portal with {self.fbid},"
                          " cleaning up and deleting...")
            await self.cleanup_and_delete()
        else:
            self.log.debug(f"{user.mxid} left portal to {self.fbid}")

    async def handle_matrix_typing(self, users: Set['u.User']) -> None:
        stopped_typing = [user.setTypingStatus(TypingStatus.STOPPED, self.fbid, self.fb_type)
                          for user in self._typing - users]
        started_typing = [user.setTypingStatus(TypingStatus.TYPING, self.fbid, self.fb_type)
                          for user in users - self._typing]
        self._typing = users
        await asyncio.gather(*stopped_typing, *started_typing, loop=self.loop)

    # endregion
    # region Facebook event handling

    async def _bridge_own_message_pm(self, source: 'u.User', sender: 'p.Puppet', mid: str) -> bool:
        if self.is_direct and sender.fbid == source.uid and not sender.is_real_user:
            if self.invite_own_puppet_to_pm:
                await self.main_intent.invite_user(self.mxid, sender.mxid)
            elif self.az.state_store.get_membership(self.mxid, sender.mxid) != Membership.JOIN:
                self.log.warn(f"Ignoring own {mid} in private chat because own puppet is not in"
                              " room.")
                return False
        return True

    async def handle_facebook_message(self, source: 'u.User', sender: 'p.Puppet',
                                      message: FBMessage) -> None:
        async with self.optional_send_lock(sender.fbid):
            if message.uid in self._dedup:
                await source.markAsDelivered(self.fbid, message.uid)
                return
            self._dedup.appendleft(message.uid)
        if not self.mxid:
            mxid = await self.create_matrix_room(source)
            if not mxid:
                # Failed to create
                return
        if not await self._bridge_own_message_pm(source, sender, f"message {message.uid}"):
            return
        intent = sender.intent_for(self)
        event_ids = []
        if message.sticker:
            event_ids = [await self._handle_facebook_sticker(intent, message.sticker,
                                                             message.reply_to_id)]
        elif len(message.attachments) > 0:
            event_ids = await asyncio.gather(
                *[self._handle_facebook_attachment(intent, attachment, message.reply_to_id)
                  for attachment in message.attachments])
            event_ids = [event_id for event_id in event_ids if event_id]
        if not event_ids and message.text:
            event_ids = [await self._handle_facebook_text(intent, message)]
        else:
            self.log.warn(f"Unhandled Messenger message: {message}")
        DBMessage.bulk_create(fbid=message.uid, fb_receiver=self.fb_receiver, mx_room=self.mxid,
                              event_ids=[event_id for event_id in event_ids if event_id])
        await source.markAsDelivered(self.fbid, message.uid)

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

    async def _handle_facebook_text(self, intent: IntentAPI, message: FBMessage) -> EventID:
        content = facebook_to_matrix(message)
        await self._add_facebook_reply(content, message.reply_to_id)
        return await intent.send_message(self.mxid, content)

    async def _handle_facebook_sticker(self, intent: IntentAPI, sticker: FBSticker,
                                       reply_to: str) -> EventID:
        # TODO handle animated stickers?
        mxc, mime, size = await self._reupload_fb_photo(sticker.url, intent)
        return await intent.send_sticker(room_id=self.mxid, url=mxc,
                                         info=ImageInfo(width=sticker.width,
                                                        height=sticker.height,
                                                        mimetype=mime,
                                                        size=size),
                                         text=sticker.label,
                                         relates_to=self._get_facebook_reply(reply_to))

    async def _handle_facebook_attachment(self, intent: IntentAPI, attachment: AttachmentClass,
                                          reply_to: str) -> Optional[EventID]:
        if isinstance(attachment, AudioAttachment):
            mxc, mime, size = await self._reupload_fb_photo(attachment.url, intent,
                                                            attachment.filename)
            event_id = await intent.send_file(self.mxid, mxc, file_type=MessageType.AUDIO,
                                              info=AudioInfo(size=size, mimetype=mime,
                                                             duration=attachment.duration),
                                              file_name=attachment.filename,
                                              relates_to=self._get_facebook_reply(reply_to))
        # elif isinstance(attachment, VideoAttachment):
        # TODO
        elif isinstance(attachment, FileAttachment):
            mxc, mime, size = await self._reupload_fb_photo(attachment.url, intent,
                                                            attachment.name)
            event_id = await intent.send_file(self.mxid, mxc,
                                              info=FileInfo(size=size, mimetype=mime),
                                              file_name=attachment.name,
                                              relates_to=self._get_facebook_reply(reply_to))
        elif isinstance(attachment, ImageAttachment):
            mxc, mime, size = await self._reupload_fb_photo(attachment.large_preview_url, intent)
            info = ImageInfo(size=size, mimetype=mime,
                             width=attachment.large_preview_width,
                             height=attachment.large_preview_height)
            event_id = await intent.send_image(self.mxid, mxc, info=info,
                                               file_name=f"image.{attachment.original_extension}",
                                               relates_to=self._get_facebook_reply(reply_to))
        elif isinstance(attachment, LocationAttachment):
            content = await self._convert_facebook_location(intent, attachment)
            content.relates_to = self._get_facebook_reply(reply_to)
            event_id = await intent.send_message(self.mxid, content)
        else:
            self.log.warn(f"Unsupported attachment type: {attachment}")
            return None
        self._last_bridged_mxid = event_id
        return event_id

    async def _convert_facebook_location(self, intent: IntentAPI, location: LocationAttachment
                                         ) -> LocationMessageEventContent:
        long, lat = location.longitude, location.latitude
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = round(long, 5)
        rounded_lat = round(lat, 5)

        text = f"{rounded_lat}° {lat_char}, {rounded_long}° {long_char}"
        url = f"https://maps.google.com/?q={lat},{long}"

        thumbnail_url, mime, size = await self._reupload_fb_photo(location.image_url, intent)
        thumbnail_info = ThumbnailInfo(mimetype=mime, width=location.image_width,
                                       height=location.image_height, size=size)
        content = LocationMessageEventContent(
            body=f"{location.address}\nLocation: {text}\n{url}", geo_uri=f"geo:{lat},{long}",
            msgtype=MessageType.LOCATION, info=LocationInfo(thumbnail_url=thumbnail_url,
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
        await sender.intent_for(self).mark_read(self.mxid, self._last_bridged_mxid)

    async def handle_facebook_typing(self, source: 'u.User', sender: 'p.Puppet') -> None:
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
        photo_url = await source.fetchImageUrl(new_photo_id)
        photo_id = self._get_photo_id(photo_url)
        if self.photo_id == photo_id:
            return
        self.photo_id = photo_id
        self._avatar_uri, _, _ = await self._reupload_fb_photo(photo_url, sender.intent)
        try:
            event_id = await sender.intent.set_room_avatar(self.mxid, self._avatar_uri)
        except IntentError:
            event_id = await self.main_intent.set_room_avatar(self.mxid, self._avatar_uri)
        DBMessage(mxid=event_id, mx_room=self.mxid,
                  fbid=message_id, fb_receiver=self.fb_receiver,
                  index=0).insert()

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
        DBMessage(mxid=event_id, mx_room=self.mxid,
                  fbid=message_id, fb_receiver=self.fb_receiver,
                  index=0).insert()

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
            self.log.debug(f"Ignoring reaction to unknown message {message}")
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
            self.log.debug(f"redacting {reaction.mxid}")
            try:
                await sender.intent_for(self).redact(reaction.mx_room, reaction.mxid)
            except MForbidden:
                await self.main_intent.redact(reaction.mx_room, reaction.mxid)
            reaction.delete()

    # endregion
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
    def get_by_thread(cls, thread: Thread, fb_receiver: Optional[str] = None) -> 'Portal':
        return cls.get_by_fbid(thread.uid, fb_receiver, thread.type)

    # endregion


def init(context: 'Context') -> None:
    global config
    Portal.az, config, Portal.loop = context.core
    Portal.invite_own_puppet_to_pm = config["bridge.invite_own_puppet_to_pm"]
