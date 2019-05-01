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
import asyncio
import re

from fbchat import User as FBUser
from mautrix.types import UserID
from mautrix.appservice import AppService, IntentAPI

from .config import Config
from .db import Puppet as DBPuppet
from . import user as u, portal as p

if TYPE_CHECKING:
    from .context import Context

config: Config


class Puppet:
    az: AppService
    loop: asyncio.AbstractEventLoop
    _mxid_prefix: str
    _mxid_suffix: str

    by_fbid: Dict[str, 'Puppet'] = {}

    fbid: str
    name: str
    photo_id: str

    is_registered: bool

    _db_instance: Optional[DBPuppet]

    intent: IntentAPI

    def __init__(self, fbid: str, name: str = "", photo_id: str = "", is_registered: bool = False,
                 db_instance: Optional[DBPuppet] = None):
        self.fbid = fbid
        self.name = name
        self.photo_id = photo_id

        self.is_registered = is_registered

        self._db_instance = db_instance

        self.mxid = self.get_mxid_from_id(fbid)
        self.intent = self.az.intent.user(self.mxid)

        self.by_fbid[fbid] = self

    # region DB conversion

    @property
    def db_instance(self) -> DBPuppet:
        if not self._db_instance:
            self._db_instance = DBPuppet(fbid=self.fbid, name=self.name, photo_id=self.photo_id,
                                         matrix_registered=self.is_registered)
        return self._db_instance

    @classmethod
    def from_db(cls, db_puppet: DBPuppet) -> 'Puppet':
        return Puppet(fbid=db_puppet.fbid, name=db_puppet.name, photo_id=db_puppet.photo_id,
                      is_registered=db_puppet.matrix_registered, db_instance=db_puppet)

    def save(self) -> None:
        self.db_instance.edit(name=self.name, photo_id=self.photo_id,
                              matrix_registered=self.is_registered)

    # endregion
    # region User info updating

    async def update_info(self, source: Optional['u.User'] = None, info: Optional[FBUser] = None
                          ) -> None:
        if not info:
            info = (await source.fetchUserInfo(self.fbid))[self.fbid]
        changed = any(await asyncio.gather(self._update_name(info),
                                           self._update_photo(info.photo),
                                           loop=self.loop))
        if changed:
            self.save()

    async def _update_name(self, info: FBUser) -> bool:
        # TODO more precise name control
        if info.name != self.name:
            self.name = info.name
            await self.intent.set_displayname(self.name)
            return True
        return False

    async def _update_photo(self, photo_url: str) -> bool:
        photo_id = p.Portal._get_photo_id(photo_url)
        if photo_id != self.photo_id:
            self.photo_id = photo_id
            if photo_url:
                avatar_uri, _, _ = await p.Portal._reupload_fb_photo(photo_url, self.intent)
            else:
                avatar_uri = ""
            await self.intent.set_avatar_url(avatar_uri)
            return True
        return False

    # endregion
    # region Getters

    @classmethod
    def get(cls, fbid: str, create: bool = True) -> Optional['Puppet']:
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            pass

        db_puppet = DBPuppet.get_by_fbid(fbid)
        if db_puppet:
            return cls.from_db(db_puppet)

        if create:
            puppet = cls(fbid)
            puppet.db_instance.insert()
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
        prefix = cls._mxid_prefix
        suffix = cls._mxid_suffix
        if mxid[:len(prefix)] == prefix and mxid[-len(suffix):] == suffix:
            return mxid[len(prefix):-len(suffix)]
        return None

    @classmethod
    def get_mxid_from_id(cls, fbid: str) -> UserID:
        return UserID(cls._mxid_prefix + fbid + cls._mxid_suffix)

    # endregion


def init(context: 'Context') -> None:
    global config
    Puppet.az, config, Puppet.loop = context.core
    username_template = config["bridge.username_template"].lower()
    index = username_template.index("{userid}")
    length = len("{userid}")
    hs_domain = config["homeserver"]["domain"]
    Puppet._mxid_prefix = f"@{username_template[:index]}"
    Puppet._mxid_suffix = f"{username_template[index + length:]}:{hs_domain}"
