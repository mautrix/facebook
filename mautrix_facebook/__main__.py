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

from mautrix.types import UserID, RoomID
from mautrix.bridge import Bridge

from .config import Config
from .db import init as init_db
from .user import User, init as init_user
from .portal import Portal, init as init_portal
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
        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())

    def _prepare_website(self) -> None:
        self.public_website = PublicBridgeWebsite(self.config["appservice.public.shared_secret"])
        self.az.app.add_subapp(self.config["appservice.public.prefix"], self.public_website.app)

    def prepare_stop(self) -> None:
        self.periodic_reconnect_task.cancel()
        self.log.debug("Stopping puppet syncers")
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.log.debug("Stopping facebook listeners")
        User.shutdown = True
        for user in User.by_fbid.values():
            user.stop_listening()

    def prepare_shutdown(self) -> None:
        self.log.debug("Saving user sessions")
        for user in User.by_mxid.values():
            user.save()

    async def start(self) -> None:
        await super().start()
        self.periodic_reconnect_task = self.loop.create_task(self._try_periodic_reconnect_loop())

    async def resend_bridge_info(self) -> None:
        self.config["bridge.resend_bridge_info"] = False
        self.config.save()
        self.log.info("Re-sending bridge info state event to all portals")
        for portal in Portal.all():
            await portal.update_bridge_info()
        self.log.info("Finished re-sending bridge info state events")

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

    async def get_portal(self, room_id: RoomID) -> Portal:
        return Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID, create: bool = False) -> Puppet:
        return await Puppet.get_by_mxid(user_id, create=create)

    async def get_double_puppet(self, user_id: UserID) -> Puppet:
        return await Puppet.get_by_custom_mxid(user_id)

    async def get_user(self, user_id: UserID, create: bool = True) -> User:
        return User.get_by_mxid(user_id, create=create)

    def is_bridge_ghost(self, user_id: UserID) -> bool:
        return bool(Puppet.get_id_from_mxid(user_id))


MessengerBridge().run()
