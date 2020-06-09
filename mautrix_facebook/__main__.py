# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
import asyncio
import logging

from mautrix.bridge import Bridge

from .config import Config
from .db import init as init_db
from .sqlstatestore import SQLStateStore
from .user import User, init as init_user
from .portal import init as init_portal
from .puppet import Puppet, init as init_puppet
from .matrix import MatrixHandler
from .context import Context
from .version import version, linkified_version
from .web import PublicBridgeWebsite


class MessengerBridge(Bridge):
    name = "mautrix-facebook"
    module = "mautrix_facebook"
    command = "python -m mautrix-facebook"
    description = "A Matrix-Facebook Messenger puppeting bridge."
    repo_url = "https://github.com/tulir/mautrix-facebook"
    real_user_content_key = "net.maunium.facebook.puppet"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler
    state_store_class = SQLStateStore

    config: Config
    public_website: PublicBridgeWebsite

    periodic_reconnect_task: asyncio.Task

    def prepare_bridge(self) -> None:
        init_db(self.db)
        context = Context(az=self.az, config=self.config, loop=self.loop, bridge=self)
        self.matrix = context.mx = MatrixHandler(context)
        self.add_startup_actions(init_user(context))
        init_portal(context)
        self.add_startup_actions(init_puppet(context))
        self._prepare_website()

    def _prepare_website(self) -> None:
        self.public_website = PublicBridgeWebsite(self.config["appservice.public.shared_secret"])
        self.az.app.add_subapp(self.config["appservice.public.prefix"], self.public_website.app)

    def prepare_shutdown(self) -> None:
        self.periodic_reconnect_task.cancel()
        self.log.debug("Stopping puppet syncers")
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.log.debug("Saving user sessions and stopping listeners")
        for mxid, user in User.by_mxid.items():
            user.stop_listening()
            user.save()

    async def start(self) -> None:
        await super().start()
        self.periodic_reconnect_task = self.loop.create_task(self._try_periodic_reconnect_loop())

    async def _try_periodic_reconnect_loop(self) -> None:
        try:
            await self._periodic_reconnect_loop()
        except Exception:
            self.log.exception("Fatal error in periodic reconnect loop")

    async def _periodic_reconnect_loop(self) -> None:
        log = logging.getLogger("mau.periodic_reconnect")
        always_reconnect = self.config["bridge.periodic_reconnect.always"]
        interval = self.config["bridge.periodic_reconnect.interval"]
        if interval <= 0:
            log.debug("Periodic reconnection is not enabled")
            return
        mode = self.config["bridge.periodic_reconnect.mode"].lower()
        if mode != "refresh" and mode != "reconnect":
            log.error("Invalid periodic reconnect mode '%s'", mode)
            return
        elif interval < 600:
            log.warning("Periodic reconnect interval is quite low (%d)", interval)
        log.debug("Starting periodic reconnect loop")
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                log.debug("Periodic reconnect loop stopped")
                return
            log.info("Executing periodic reconnections")
            for user in User.by_fbid.values():
                if not user.is_connected and not always_reconnect:
                    log.debug("Not reconnecting %s: not connected", user.mxid)
                    continue
                log.debug("Executing periodic reconnect for %s", user.mxid)
                try:
                    if mode == "refresh":
                        await user.refresh()
                    elif mode == "reconnect":
                        user.listener.disconnect()
                        await user.listen_task
                        user.start_listen()
                except asyncio.CancelledError:
                    log.debug("Periodic reconnect loop stopped")
                    return
                except Exception:
                    log.exception("Error while reconnecting", user.mxid)


MessengerBridge().run()
