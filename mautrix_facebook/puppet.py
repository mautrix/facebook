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
from typing import (Optional, Dict, AsyncGenerator, AsyncIterable, Awaitable, Union, TYPE_CHECKING,
                    cast)
from datetime import datetime, timedelta
import asyncio

from yarl import URL
import magic

from mautrix.types import UserID, RoomID, SyncToken, ContentURI
from mautrix.appservice import IntentAPI
from mautrix.bridge import BasePuppet, async_getter_lock
from mautrix.util.simple_template import SimpleTemplate
from maufbapi.types.graphql import Participant, Picture

from .config import Config
from .db import Puppet as DBPuppet
from . import user as u, portal as p, matrix as m

if TYPE_CHECKING:
    from .__main__ import MessengerBridge


class Puppet(DBPuppet, BasePuppet):
    mx: m.MatrixHandler
    config: Config
    hs_domain: str
    mxid_template: SimpleTemplate[int]

    by_fbid: Dict[int, 'Puppet'] = {}
    by_custom_mxid: Dict[UserID, 'Puppet'] = {}

    _last_info_sync: Optional[datetime]

    def __init__(self, fbid: int, name: Optional[str] = None, photo_id: Optional[str] = None,
                 photo_mxc: Optional[ContentURI] = None, name_set: bool = False,
                 avatar_set: bool = False, is_registered: bool = False,
                 custom_mxid: Optional[UserID] = None, access_token: Optional[str] = None,
                 next_batch: Optional[SyncToken] = None, base_url: Optional[URL] = None) -> None:
        super().__init__(fbid, name, photo_id, photo_mxc, name_set, avatar_set, is_registered,
                         custom_mxid, access_token, next_batch, base_url)
        self._last_info_sync = None

        self.default_mxid = self.get_mxid_from_id(fbid)
        self.default_mxid_intent = self.az.intent.user(self.default_mxid)
        self.intent = self._fresh_intent()

        self.log = self.log.getChild(str(self.fbid))

    @property
    def should_sync(self) -> bool:
        now = datetime.now()
        if not self._last_info_sync or now - self._last_info_sync > timedelta(hours=48):
            self._last_info_sync = now
            return True
        return False

    async def default_puppet_should_leave_room(self, room_id: RoomID) -> bool:
        portal = await p.Portal.get_by_mxid(room_id)
        return portal and portal.fbid != self.fbid

    async def _leave_rooms_with_default_user(self) -> None:
        await super()._leave_rooms_with_default_user()
        # Make the user join all private chat portals.
        await asyncio.gather(*[self.intent.ensure_joined(portal.mxid)
                               async for portal in p.Portal.get_all_by_receiver(self.fbid)
                               if portal.mxid], loop=self.loop)

    def intent_for(self, portal: 'p.Portal') -> IntentAPI:
        if portal.fbid == self.fbid or (portal.backfill_lock.locked
                                        and self.config["bridge.backfill.invite_own_puppet"]):
            return self.default_mxid_intent
        return self.intent

    @classmethod
    def init_cls(cls, bridge: 'MessengerBridge') -> AsyncIterable[Awaitable[None]]:
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.mx = bridge.matrix
        cls.az = bridge.az
        cls.hs_domain = cls.config["homeserver.domain"]
        cls.mxid_template = SimpleTemplate(cls.config["bridge.username_template"], "userid",
                                           prefix="@", suffix=f":{Puppet.hs_domain}", type=int)
        cls.sync_with_custom_puppets = cls.config["bridge.sync_with_custom_puppets"]
        cls.homeserver_url_map = {server: URL(url) for server, url
                                  in cls.config["bridge.double_puppet_server_map"].items()}
        cls.allow_discover_url = cls.config["bridge.double_puppet_allow_discovery"]
        cls.login_shared_secret_map = {server: secret.encode("utf-8") for server, secret
                                       in cls.config["bridge.login_shared_secret_map"].items()}
        cls.login_device_name = "Facebook Messenger Bridge"

        return (puppet.try_start() async for puppet in Puppet.get_all_with_custom_mxid())

    # region User info updating

    async def update_info(self, source: Optional['u.User'] = None, info: Participant = None,
                          update_avatar: bool = True) -> 'Puppet':
        if not info:
            # if not self.should_sync:
            #     return self
            # FIXME
            # info = await source.client.fetch_thread_info([self.fbid]).__anext__()
            print("no info to update puppet :(")
            return self
        self._last_info_sync = datetime.now()
        try:
            changed = await self._update_name(info)
            if update_avatar:
                changed = await self._update_photo(source, info.profile_pic_large) or changed
            if changed:
                await self.save()
        except Exception:
            self.log.exception(f"Failed to update info from source {source.fbid}")
        return self

    @classmethod
    def _get_displayname(cls, info: Participant) -> str:
        sn = info.structured_name
        info = {
            "displayname": None,
            "id": info.id,
            "name": info.name,
            "phonetic_name": sn.phonetic_name if sn else None,
            "own_nickname": info.nickname_for_viewer,
            **(sn.to_dict() if sn else {}),
        }
        for preference in cls.config["bridge.displayname_preference"]:
            if info.get(preference):
                info["displayname"] = info.get(preference)
                break
        return cls.config["bridge.displayname_template"].format(**info)

    async def _update_name(self, info: Participant) -> bool:
        name = self._get_displayname(info)
        if name != self.name or not self.name_set:
            self.name = name
            try:
                await self.default_mxid_intent.set_displayname(self.name)
                self.name_set = True
            except Exception:
                self.log.exception("Failed to set displayname")
                self.name_set = False
            return True
        return False

    @staticmethod
    async def reupload_avatar(source: Optional['u.User'], intent: IntentAPI, url: str,
                              fbid: int, use_graph: bool = True) -> ContentURI:
        data = None
        if use_graph and source and source.state and source.state.session.access_token:
            graph_url = ((source.client.graph_url / str(fbid) / "picture")
                         .with_query({"width": "1000", "height": "1000"}))
            async with source.client.get(graph_url) as resp:
                if resp.status < 400:
                    data = await resp.read()
        if data is None:
            async with source.client.get(url) as resp:
                data = await resp.read()
        mime = magic.from_buffer(data, mime=True)
        return await intent.upload_media(data, mime_type=mime)

    async def _update_photo(self, source: 'u.User', photo: Picture) -> bool:
        photo_id = p.Portal.get_photo_id(photo)
        if photo_id != self.photo_id or not self.avatar_set:
            self.photo_id = photo_id
            if photo:
                self.photo_mxc = await self.reupload_avatar(source, self.default_mxid_intent,
                                                            photo.uri, self.fbid,
                                                            use_graph=(photo.height or 0) < 500)
            else:
                self.photo_mxc = ContentURI("")
            try:
                await self.default_mxid_intent.set_avatar_url(self.photo_mxc)
                self.avatar_set = True
            except Exception:
                self.log.exception("Failed to set avatar")
                self.avatar_set = False
            return True
        return False

    # endregion
    # region Database getters

    def _add_to_cache(self) -> None:
        self.by_fbid[self.fbid] = self
        if self.custom_mxid:
            self.by_custom_mxid[self.custom_mxid] = self

    @classmethod
    @async_getter_lock
    async def get_by_fbid(cls, fbid: Union[str, int], *, create: bool = True) -> Optional['Puppet']:
        if isinstance(fbid, str):
            fbid = int(fbid)
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            pass

        puppet = cast(cls, await super().get_by_fbid(fbid))
        if puppet:
            puppet._add_to_cache()
            return puppet

        if create:
            puppet = cls(fbid)
            await puppet.insert()
            puppet._add_to_cache()
            return puppet

        return None

    @classmethod
    async def get_by_mxid(cls, mxid: UserID, create: bool = True) -> Optional['Puppet']:
        fbid = cls.get_id_from_mxid(mxid)
        if fbid:
            return await cls.get_by_fbid(fbid, create=create)
        return None

    @classmethod
    @async_getter_lock
    async def get_by_custom_mxid(cls, mxid: UserID) -> Optional['Puppet']:
        try:
            return cls.by_custom_mxid[mxid]
        except KeyError:
            pass

        puppet = cast(cls, await super().get_by_custom_mxid(mxid))
        if puppet:
            puppet._add_to_cache()
            return puppet

        return None

    @classmethod
    def get_id_from_mxid(cls, mxid: UserID) -> Optional[int]:
        return cls.mxid_template.parse(mxid)

    @classmethod
    def get_mxid_from_id(cls, fbid: int) -> UserID:
        return UserID(cls.mxid_template.format_full(fbid))

    @classmethod
    async def get_all_with_custom_mxid(cls) -> AsyncGenerator['Puppet', None]:
        puppets = await super().get_all_with_custom_mxid()
        puppet: cls
        for puppet in puppets:
            try:
                yield cls.by_fbid[puppet.fbid]
            except KeyError:
                puppet._add_to_cache()
                yield puppet

    # endregion
