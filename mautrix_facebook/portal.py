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
from typing import Dict, Optional, Tuple, Union, TYPE_CHECKING
import asyncio
import logging

from yarl import URL
import aiohttp
import magic

from fbchat.models import (ThreadType, Thread, User as FBUser, Group as FBGroup, Page as FBPage,
                           Message as FBMessage, Sticker as FBSticker, AudioAttachment,
                           VideoAttachment, FileAttachment, ImageAttachment, LocationAttachment,
                           ShareAttachment)
from mautrix.types import (RoomID, EventType, ContentURI, MessageEventContent, EventID,
                           ImageInfo, MessageType, LocationMessageEventContent, LocationInfo,
                           ThumbnailInfo, FileInfo, AudioInfo, VideoInfo, Format)
from mautrix.appservice import AppService, IntentAPI
from mautrix.errors import MForbidden

from .config import Config
from . import puppet as p, user as u

if TYPE_CHECKING:
    from .context import Context

config: Config

ThreadClass = Union[FBUser, FBGroup, FBPage]
AttachmentClass = Union[AudioAttachment, VideoAttachment, FileAttachment, ImageAttachment,
                        LocationAttachment, ShareAttachment]


class Portal:
    az: AppService
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.portal")
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_fbid: Dict[Tuple[str, str], 'Portal'] = {}

    fbid: str
    fb_receiver: str
    fb_type: ThreadType
    mxid: Optional[RoomID]

    name: str
    photo_id: str
    avatar_uri: ContentURI

    messages_by_fbid: Dict[str, Optional[EventID]]
    messages_by_mxid: Dict[EventID, Optional[str]]
    last_bridged_mxid: EventID

    _main_intent: Optional[IntentAPI]
    _create_room_lock: asyncio.Lock

    def __init__(self, fbid: str, fb_receiver: str, fb_type: ThreadType,
                 mxid: Optional[RoomID] = None,
                 name: str = "", photo_id: str = "", avatar_uri: ContentURI = "") -> None:
        self.fbid = fbid
        self.fb_receiver = fb_receiver
        self.fb_type = fb_type
        self.mxid = mxid

        self.name = name
        self.photo_id = photo_id
        self.avatar_uri = avatar_uri

        self._main_intent = None
        self._create_room_lock = asyncio.Lock()

        self.messages_by_fbid = {}
        self.messages_by_mxid = {}

        self.log = self.log.getChild(self.fbid_log)

        self.by_fbid[self.fbid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self

    def to_dict(self) -> Dict[str, str]:
        return {
            "fbid": self.fbid,
            "fb_type": self.fb_type.value,
            "fb_receiver": self.fb_receiver,
            "mxid": self.mxid,
            "name": self.name,
            "photo_id": self.photo_id,
            "avatar_uri": self.avatar_uri,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'Portal':
        return cls(fbid=data["fbid"], fb_receiver=data["fb_receiver"],
                   fb_type=ThreadType(data["fb_type"]), mxid=RoomID(data["mxid"]),
                   name=data["name"], photo_id=data["photo_id"],
                   avatar_uri=ContentURI(data["avatar_uri"]))

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
            self._main_intent = (p.Puppet.get(self.fbid).intent
                                 if self.is_direct
                                 else self.az.intent)

        return self._main_intent

    async def update_info(self, source: Optional['u.User'] = None,
                          info: Optional[ThreadClass] = None) -> ThreadClass:
        if not info:
            info = (await source.fetchThreadInfo(self.fbid))[self.fbid]
        await asyncio.gather(self._update_name(info.name),
                             self._update_photo(info.photo),
                             self._update_participants(source, info),
                             loop=self.loop)
        return info

    @staticmethod
    def _get_photo_id(url: str) -> str:
        path = URL(url).path
        return path[path.rfind("/") + 1:]

    @staticmethod
    async def _reupload_photo(url: str, intent: IntentAPI, filename: Optional[str] = None
                              ) -> Tuple[ContentURI, str, int]:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(url)
            data = await resp.read()
        mime = magic.from_buffer(data, mime=True)
        return await intent.upload_media(data, mime_type=mime, filename=filename), mime, len(data)

    async def _update_name(self, name: str) -> None:
        if self.name != name:
            self.name = name
            if self.mxid and not self.is_direct:
                await self.main_intent.set_room_name(self.mxid, self.name)

    async def _update_photo(self, photo_url: str) -> None:
        photo_id = self._get_photo_id(photo_url)
        print(photo_id, self.photo_id)
        if self.photo_id != photo_id or len(self.avatar_uri) == 0:
            self.photo_id = photo_id
            if self.mxid and not self.is_direct:
                self.avatar_uri, _, _ = await self._reupload_photo(photo_url, self.main_intent)
                await self.main_intent.set_room_avatar(self.mxid, self.avatar_uri)

    async def _update_participants(self, source: 'u.User', info: ThreadClass) -> None:
        if self.is_direct:
            await p.Puppet.get(info.uid).update_info(source=source, info=info)
            return
        elif not self.mxid:
            return
        users = await source.fetchAllUsersFromThreads([info])
        puppets = {user: p.Puppet.get(user.uid) for user in users}
        await asyncio.gather(*[puppet.update_info(source=source, info=user)
                               for user, puppet in puppets.items()])
        await asyncio.gather(*[puppet.intent.ensure_joined(self.mxid)
                               for puppet in puppets.values()])

    async def _update_matrix_room(self, source: 'u.User',
                                  info: Optional[ThreadClass] = None) -> None:
        await self.main_intent.invite_user(self.mxid, source.mxid)

    async def create_matrix_room(self, source: 'u.User', info: Optional[ThreadClass] = None
                                 ) -> RoomID:
        if self.mxid:
            await self._update_matrix_room(source, info)
            return self.mxid
        async with self._create_room_lock:
            await self._create_matrix_room(source, info)

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
                                  "content": {"avatar_url": self.avatar_uri}})
        self.mxid = await self.main_intent.create_room(name=name, is_direct=self.is_direct,
                                                       initial_state=initial_state,
                                                       invitees=[source.mxid])
        self.log.debug(f"Matrix room created: {self.mxid}")
        if not self.mxid:
            raise Exception("Failed to create room: no mxid required")
        self.by_mxid[self.mxid] = self
        if not self.is_direct:
            await self._update_participants(source, info)

    # region Matrix event handling

    async def handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                    event_id: EventID) -> None:
        if event_id in self.messages_by_mxid:
            return
        self.messages_by_mxid[event_id] = None
        fbid = await sender.send(FBMessage(text=message.body), self.fbid, self.fb_type)
        self.messages_by_fbid[fbid] = event_id
        self.messages_by_mxid[event_id] = fbid
        self.last_bridged_mxid = event_id

    async def handle_matrix_redaction(self, sender: 'u.User', event_id: EventID) -> None:
        if not self.mxid:
            return
        try:
            message_id = self.messages_by_mxid[event_id]
        except KeyError:
            return
        if message_id is None:
            return
        self.messages_by_mxid[event_id] = None
        self.messages_by_fbid[message_id] = None
        try:
            await sender.unsend(message_id)
        except Exception:
            self.log.exception("Unsend failed")

    # endregion
    # region Facebook event handling

    async def handle_facebook_message(self, source: 'u.User', sender: 'p.Puppet',
                                      message: FBMessage) -> None:
        if message.uid in self.messages_by_fbid:
            await source.markAsDelivered(self.fbid, message.uid)
            return
        if not self.mxid:
            await self.create_matrix_room(source)
        self.messages_by_fbid[message.uid] = None
        if message.sticker is not None:
            event_id = await self._handle_facebook_sticker(sender.intent, message.sticker)
        elif len(message.attachments) > 0:
            event_ids = await asyncio.gather(
                *[self._handle_facebook_attachment(sender.intent, attachment)
                  for attachment in message.attachments])
            event_id = event_ids[-1]
        else:
            event_id = await self._handle_facebook_text(sender.intent, message)
        if not event_id:
            return
        self.messages_by_mxid[event_id] = message.uid
        self.messages_by_fbid[message.uid] = event_id
        self.last_bridged_mxid = event_id
        await source.markAsDelivered(self.fbid, message.uid)

    async def _handle_facebook_text(self, intent: IntentAPI, message: FBMessage) -> EventID:
        return await intent.send_text(self.mxid, message.text)

    async def _handle_facebook_sticker(self, intent: IntentAPI, sticker: FBSticker) -> EventID:
        # TODO handle animated stickers?
        mxc, mime, size = await self._reupload_photo(sticker.url, intent)
        return await intent.send_sticker(room_id=self.mxid, url=mxc,
                                         info=ImageInfo(width=sticker.width,
                                                        height=sticker.height,
                                                        mimetype=mime,
                                                        size=size),
                                         text=sticker.label)

    async def _handle_facebook_attachment(self, intent: IntentAPI, attachment: AttachmentClass
                                          ) -> EventID:
        if isinstance(attachment, AudioAttachment):
            mxc, mime, size = await self._reupload_photo(attachment.url, intent,
                                                         attachment.filename)
            return await intent.send_file(self.mxid, mxc, file_type=MessageType.AUDIO,
                                          info=AudioInfo(size=size, mimetype=mime,
                                                         duration=attachment.duration),
                                          file_name=attachment.filename,)
        elif isinstance(attachment, VideoAttachment):
            self.log.warn("Unsupported attachment type:", attachment)
            return None
        elif isinstance(attachment, FileAttachment):
            mxc, mime, size = await self._reupload_photo(attachment.url, intent, attachment.name)
            return await intent.send_file(self.mxid, mxc,
                                          info=FileInfo(size=size, mimetype=mime),
                                          file_name=attachment.name)
        elif isinstance(attachment, ImageAttachment):
            self.log.warn("Unsupported attachment type:", attachment)
            #mxc, mime, size = await self._reupload_photo(attachment, intent)
            return None
        elif isinstance(attachment, LocationAttachment):
            content = await self._convert_facebook_location(intent, attachment)
            return await intent.send_message(self.mxid, content)
        else:
            self.log.warn("Unsupported attachment type:", attachment)
            return None

    async def _convert_facebook_location(self, intent: IntentAPI, location: LocationAttachment
                                         ) -> LocationMessageEventContent:
        long, lat = location.longitude, location.latitude
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = round(long, 5)
        rounded_lat = round(lat, 5)

        text = f"{rounded_lat}° {lat_char}, {rounded_long}° {long_char}"
        url = f"https://maps.google.com/?q={lat},{long}"

        thumbnail_url, mime, size = await self._reupload_photo(location.image_url, intent)
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
        try:
            event_id = self.messages_by_fbid[message_id]
        except KeyError:
            return
        if event_id is None:
            return
        self.messages_by_fbid[message_id] = None
        self.messages_by_mxid[event_id] = None
        # Facebook only allows unsending own messages, so it should be safe to use the deleter
        # intent to redact even without power level sync.
        try:
            await sender.intent.redact(self.mxid, event_id)
        except MForbidden:
            await self.main_intent.redact(self.mxid, event_id)

    async def handle_facebook_seen(self, source: 'u.User', sender: 'p.Puppet') -> None:
        if not self.mxid:
            return
        await sender.intent.mark_read(self.mxid, self.last_bridged_mxid)

    async def handle_facebook_typing(self, source: 'u.User', sender: 'p.Puppet') -> None:
        pass

    # endregion

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass
        return None

    @classmethod
    def get_by_fbid(cls, fbid: str, fb_receiver: Optional[str] = None,
                    fb_type: Optional[ThreadType] = None) -> Optional['Portal']:
        fb_receiver = fb_receiver or fbid
        fbid_full = (fbid, fb_receiver)
        try:
            return cls.by_fbid[fbid_full]
        except KeyError:
            if fb_type:
                return cls(fbid=fbid, fb_receiver=fb_receiver, fb_type=fb_type)
        return None

    @classmethod
    def get_by_thread(cls, thread: Thread, fb_receiver: Optional[str] = None) -> 'Portal':
        return cls.get_by_fbid(thread.uid, fb_receiver, thread.type)


def init(context: 'Context') -> None:
    global config
    Portal.az, config, Portal.loop = context.core
