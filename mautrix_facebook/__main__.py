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
from itertools import chain

from mautrix.bridge import Bridge

from .config import Config
from .db import init as init_db
from .sqlstatestore import SQLStateStore
from .user import User, init as init_user
from .portal import init as init_portal
from .puppet import Puppet, init as init_puppet
from .matrix import MatrixHandler
from .context import Context
from . import __version__


class MessengerBridge(Bridge):
    name = "mautrix-facebook"
    command = "python -m mautrix-facebook"
    description = "A Matrix-Facebook Messenger puppeting bridge."
    real_user_content_key = "net.maunium.facebook.puppet"
    version = __version__
    config_class = Config
    matrix_class = MatrixHandler
    state_store_class = SQLStateStore

    config: Config

    def prepare_bridge(self) -> None:
        init_db(self.db)
        context = Context(az=self.az, config=self.config, loop=self.loop)
        self.matrix = context.mx = MatrixHandler(context)
        user_startup = init_user(context)
        init_portal(context)
        puppet_startup = init_puppet(context)
        self.startup_actions = chain(user_startup, puppet_startup)

    def prepare_shutdown(self) -> None:
        self.log.debug("Stopping puppet syncers")
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.log.debug("Saving user sessions and stopping listeners")
        for mxid, user in User.by_mxid.items():
            user.stopListening()
            user.save()


MessengerBridge().run()
