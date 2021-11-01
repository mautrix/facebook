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

from typing import Union
import asyncio
import logging
from mautrix.types import PresenceState
from .puppet import Puppet

PRESENCE_SYNC_TIMEOUT = 25; # synapse has a timeout of 30s, an extra 5s gives some slack

# idea stolen from <https://github.com/Sorunome/mx-puppet-bridge>
class PresenceUpdater:
    puppets = {}
    log = logging.getLogger("mau.user")

    @classmethod
    async def set_presence(cls, puppet: Puppet, state: PresenceState):
        await puppet.intent.set_presence(presence=state, ignore_cache=True)
        cls.puppets[puppet.fbid] = (puppet, state)
    
    @classmethod
    async def _refresh_presence(cls):
        for fbid, (puppet, state) in list(cls.puppets.items()):
            await puppet.intent.set_presence(presence=state, ignore_cache=True)
            
            # stop updating if the user is no longer online
            if state != PresenceState.ONLINE:
                del cls.puppets[fbid]

    @classmethod
    async def refresh_periodically(cls):
        while True:
            cls.log.trace(f"Refreshing presence for {len(cls.puppets)} puppets.")
            await asyncio.gather(
                asyncio.sleep(PRESENCE_SYNC_TIMEOUT),
                cls._refresh_presence(),
            )
