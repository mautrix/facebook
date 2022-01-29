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
from __future__ import annotations

import asyncio

from mautrix.types import PresenceState

from . import puppet as pu

# synapse has a timeout of 30s, an extra 5s gives some slack
PRESENCE_SYNC_TIMEOUT = 25


# idea taken from <https://github.com/Sorunome/mx-puppet-bridge>
class PresenceUpdater:
    puppets = {}
    running = False

    @classmethod
    async def set_presence(cls, puppet: pu.Puppet, presence: PresenceState):
        if cls.running:
            # user is online -> schedule for periodic refresh and also update now
            if presence == PresenceState.ONLINE:
                cls.puppets[puppet.fbid] = (puppet, presence)
                await puppet.intent.set_presence(presence, ignore_cache=True)
            # user is offline but scheduled for an update -> cancel it and update now
            elif puppet.fbid in cls.puppets:
                cls.puppets.pop(puppet.fbid, None)
                await puppet.intent.set_presence(presence, ignore_cache=True)

    @classmethod
    async def _refresh_presence(cls):
        for fbid, (puppet, presence) in list(cls.puppets.items()):
            await puppet.intent.set_presence(presence, ignore_cache=True)

            # stop updating if the user is no longer online
            if presence != PresenceState.ONLINE:
                cls.puppets.pop(fbid, None)

    @classmethod
    async def refresh_periodically(cls):
        cls.running = True
        while True:
            await asyncio.gather(
                asyncio.sleep(PRESENCE_SYNC_TIMEOUT),
                cls._refresh_presence(),
            )
