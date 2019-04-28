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
from typing import Dict, Optional, Union, TYPE_CHECKING
import aiohttp
import asyncio
import logging

from fbchat import (ThreadType, Thread, User as FBUser, Group as FBGroup, Page as FBPage,
                    Message as FBMessage)
from mautrix.types import RoomID, EventType, ContentURI, MessageEventContent, EventID
from mautrix.appservice import AppService, IntentAPI

from .config import Config
from . import puppet as p, user as u

if TYPE_CHECKING:
    from .context import Context

config: Config

ThreadClass = Union[FBUser, FBGroup, FBPage]


class Portal:
    az: AppService
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.portal")
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_fbid: Dict[str, 'Portal'] = {}

    fbid: str
    fb_type: ThreadType
    mxid: Optional[RoomID]

    name: str
    photo: str
    avatar_uri: ContentURI

    _main_intent: Optional[IntentAPI]

    def __init__(self, fbid: str, fb_type: ThreadType, mxid: Optional[RoomID] = None,
                 name: str = "", photo: str = "", avatar_uri: ContentURI = ""):
        self.fbid = fbid
        self.log = self.log.getChild(fbid)
        self.fb_type = fb_type
        self.by_fbid[fbid] = self
        self.mxid = mxid
        if self.mxid:
            self.by_mxid[self.mxid] = self

        self._main_intent = None

        self.name = name
        self.photo = photo
        self.avatar_uri = avatar_uri

    def to_dict(self) -> Dict[str, str]:
        return {
            "fbid": self.fbid,
            "fb_type": self.fb_type.value,
            "mxid": self.mxid,
            "name": self.name,
            "photo": self.photo,
            "avatar_uri": self.avatar_uri,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'Portal':
        return cls(fbid=data["fbid"], fb_type=ThreadType(data["fb_type"]), mxid=data["mxid"],
                   name=data["name"], photo=data["photo"],
                   avatar_uri=ContentURI(data["avatar_uri"]))

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

    async def _update_name(self, name: str) -> None:
        if self.name != name:
            self.name = name
            if self.mxid and not self.is_direct:
                await self.main_intent.set_room_name(self.mxid, self.name)

    async def _update_photo(self, photo: str) -> None:
        if self.photo != photo:
            self.photo = photo
            if self.mxid and not self.is_direct:
                async with aiohttp.ClientSession() as session:
                    resp = await session.get(self.photo)
                    data = await resp.read()
                self.avatar_uri = await self.main_intent.upload_media(data)
                await self.main_intent.set_room_avatar(self.mxid, self.avatar_uri)

    async def _update_participants(self, source: 'u.User', info: ThreadClass) -> None:
        if not self.mxid:
            return
        elif self.is_direct:
            await p.Puppet.get(info.uid).update_info(source=source, info=info)
            return
        users = await source.fetchAllUsersFromThreads(info)
        puppets = {user: p.Puppet.get(user.uid) for user in users}
        await asyncio.gather(*[puppet.update_info(source=source, info=user)
                               for user, puppet in puppets.items()])
        await asyncio.gather(*[puppet.intent.ensure_joined(self.mxid)
                               for puppet in puppets.values()])

    async def create_matrix_room(self, source: 'u.User', info: Optional[Thread] = None) -> RoomID:
        if self.mxid:
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
            raise Exception("Failed to create room")
        self.by_mxid[self.mxid] = self
        await self._update_participants(source, info)

    async def handle_matrix_message(self, sender: 'u.User', message: MessageEventContent,
                                    event_id: EventID):
        await sender.send(FBMessage(text=message.body), self.fbid, self.fb_type)

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass
        return None

    @classmethod
    def get_by_fbid(cls, fbid: str, fb_type: Optional[ThreadType] = None) -> Optional['Portal']:
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            if fb_type:
                return cls(fbid=fbid, fb_type=fb_type)
        return None

    @classmethod
    def get_by_thread(cls, thread: Thread) -> 'Portal':
        return cls.get_by_fbid(thread.uid, thread.type)


def init(context: 'Context') -> None:
    global config
    Portal.az, config, Portal.loop = context.core
