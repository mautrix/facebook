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
from typing import Dict, Tuple

from mautrix.types import UserID, RoomID, PowerLevelStateEventContent, Membership, Member
from mautrix.appservice import StateStore

from . import puppet as pu
from .db import RoomState, UserProfile


class SQLStateStore(StateStore):
    profile_cache: Dict[Tuple[RoomID, UserID], UserProfile]
    _room_state_cache: Dict[RoomID, RoomState]
    _registered: Dict[UserID, bool]

    def __init__(self) -> None:
        super().__init__()
        self.profile_cache = {}
        self._room_state_cache = {}
        self._registered = {}

    def is_registered(self, user_id: UserID) -> bool:
        puppet = pu.Puppet.get_by_mxid(user_id)
        return puppet.is_registered if puppet else self._registered.get(user_id, False)

    def registered(self, user_id: UserID) -> None:
        puppet = pu.Puppet.get_by_mxid(user_id)
        if puppet:
            puppet.is_registered = True
            puppet.save()
        else:
            self._registered[user_id] = True

    def _get_user_profile(self, room_id: RoomID, user_id: UserID, create: bool = True
                          ) -> UserProfile:
        key = (room_id, user_id)
        try:
            return self.profile_cache[key]
        except KeyError:
            pass

        profile = UserProfile.get(*key)
        if profile:
            self.profile_cache[key] = profile
        elif create:
            profile = UserProfile(room_id=room_id, user_id=user_id, membership=Membership.LEAVE)
            profile.insert()
            self.profile_cache[key] = profile
        return profile

    def get_member(self, room_id: RoomID, user_id: UserID) -> Member:
        return self._get_user_profile(room_id, user_id).member()

    def set_member(self, room_id: RoomID, user_id: UserID, member: Member) -> None:
        profile = self._get_user_profile(room_id, user_id)
        profile.membership = member.membership
        profile.displayname = member.displayname or profile.displayname
        profile.avatar_url = member.avatar_url or profile.avatar_url
        profile.update()

    def set_membership(self, room_id: RoomID, user_id: UserID, membership: Membership) -> None:
        self.set_member(room_id, user_id, Member(membership=membership))

    def _get_room_state(self, room_id: RoomID, create: bool = True) -> RoomState:
        try:
            return self._room_state_cache[room_id]
        except KeyError:
            pass

        room = RoomState.get(room_id)
        if room:
            self._room_state_cache[room_id] = room
        elif create:
            room = RoomState(room_id=room_id)
            room.insert()
            self._room_state_cache[room_id] = room
        return room

    def has_power_levels(self, room_id: RoomID) -> bool:
        return self._get_room_state(room_id).has_power_levels

    def get_power_levels(self, room_id: RoomID) -> PowerLevelStateEventContent:
        return self._get_room_state(room_id).power_levels

    def set_power_level(self, room_id: RoomID, user_id: UserID, level: int) -> None:
        room_state = self._get_room_state(room_id)
        power_levels = room_state.power_levels
        if not power_levels:
            power_levels = {
                "users": {},
                "events": {},
            }
        power_levels[room_id]["users"][user_id] = level
        room_state.power_levels = power_levels
        room_state.update()

    def set_power_levels(self, room_id: RoomID, content: PowerLevelStateEventContent) -> None:
        state = self._get_room_state(room_id)
        state.power_levels = content
        state.update()
