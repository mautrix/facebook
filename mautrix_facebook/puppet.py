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
from typing import Optional, Dict, Pattern, TYPE_CHECKING
import aiohttp
import asyncio
import re

from fbchat import User as FBUser
from mautrix.types import UserID, ContentURI
from mautrix.appservice import AppService, IntentAPI

from .config import Config
from . import user as u

if TYPE_CHECKING:
    from .context import Context

config: Config


class Puppet:
    az: AppService
    loop: asyncio.AbstractEventLoop
    username_template: str
    hs_domain: str
    mxid_regex: Pattern
    by_fbid: Dict[str, 'Puppet'] = {}

    fbid: str
    name: str
    photo: str
    avatar_uri: ContentURI

    intent: IntentAPI

    def __init__(self, fbid: str, name: str = "", photo: str = "", avatar_uri: ContentURI = ""):
        self.fbid = fbid
        self.name = name
        self.photo = photo
        self.avatar_uri = avatar_uri

        self.mxid = self.get_mxid_from_id(fbid)
        self.intent = self.az.intent.user(self.mxid)

        self.by_fbid[fbid] = self

    def to_dict(self) -> Dict[str, str]:
        return {
            "fbid": self.fbid,
            "name": self.name,
            "photo": self.photo,
            "avatar_uri": self.avatar_uri,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'Puppet':
        return cls(fbid=data["fbid"], name=data["name"], photo=data["photo"],
                   avatar_uri=ContentURI(data["avatar_uri"]))

    async def update_info(self, source: Optional['u.User'] = None, info: Optional[FBUser] = None
                          ) -> None:
        if not info:
            info = (await source.fetchUserInfo(self.fbid))[self.fbid]
        await asyncio.gather(self._update_name(info),
                             self._update_photo(info.photo),
                             loop=self.loop)

    async def _update_name(self, info: FBUser) -> None:
        # TODO more precise name control
        if info.name != self.name:
            self.name = info.name
            await self.intent.set_displayname(self.name)

    async def _update_photo(self, photo: str) -> None:
        if photo != self.photo or len(self.avatar_uri) == 0:
            self.photo = photo
            async with aiohttp.ClientSession() as session:
                resp = await session.get(self.photo)
                data = await resp.read()
            self.avatar_uri = await self.intent.upload_media(data)
            await self.intent.set_avatar_url(self.avatar_uri)

    @classmethod
    def get(cls, fbid: str, create: bool = True) -> Optional['Puppet']:
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            pass

        if create:
            puppet = cls(fbid)
            return puppet

        return None

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True) -> Optional['Puppet']:
        fbid = cls.get_id_from_mxid(mxid)
        if fbid:
            return cls.get(fbid, create)

        return None

    @classmethod
    def get_id_from_mxid(cls, mxid: UserID) -> Optional[str]:
        match = cls.mxid_regex.match(mxid)
        if match:
            return match.group(1)
        return None

    @classmethod
    def get_mxid_from_id(cls, fbid: str) -> UserID:
        return UserID(f"@{cls.username_template.format(userid=fbid)}:{cls.hs_domain}")


def init(context: 'Context') -> None:
    global config
    Puppet.az, config, Puppet.loop = context.core
    Puppet.username_template = config["bridge.username_template"]
    Puppet.hs_domain = config["homeserver"]["domain"]
    Puppet.mxid_regex = re.compile(f"@{Puppet.username_template.format(userid='(.+)')}"
                                   f":{Puppet.hs_domain}")
