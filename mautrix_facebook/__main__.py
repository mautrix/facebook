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
from typing import Optional
import asyncio
import logging

from mautrix.types import UserID, RoomID
from mautrix.bridge import Bridge
from mautrix.bridge.state_store.asyncpg import PgBridgeStateStore
from mautrix.util.async_db import Database

from .config import Config
from .db import upgrade_table, init as init_db
from .user import User
from .portal import Portal
from .puppet import Puppet
from .matrix import MatrixHandler
from .version import version, linkified_version
from .web import PublicBridgeWebsite


class MessengerBridge(Bridge):
    name = "mautrix-facebook"
    module = "mautrix_facebook"
    command = "python -m mautrix-facebook"
    description = "A Matrix-Facebook Messenger puppeting bridge."
    repo_url = "https://github.com/mautrix/facebook"
    real_user_content_key = "net.maunium.facebook.puppet"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler

    db: Database
    config: Config
    matrix: MatrixHandler
    public_website: Optional[PublicBridgeWebsite]
    state_store: PgBridgeStateStore

    periodic_reconnect_task: asyncio.Task

    def make_state_store(self) -> None:
        self.state_store = PgBridgeStateStore(self.db, self.get_puppet, self.get_double_puppet)

    def prepare_db(self) -> None:
        self.db = Database(self.config["appservice.database"], upgrade_table=upgrade_table,
                           loop=self.loop, db_args=self.config["appservice.database_opts"])
        init_db(self.db)

    def prepare_bridge(self) -> None:
        super().prepare_bridge()
        if self.config["appservice.public.enabled"]:
            secret = self.config["appservice.public.shared_secret"]
            self.public_website = PublicBridgeWebsite(loop=self.loop, shared_secret=secret)
            self.az.app.add_subapp(self.config["appservice.public.prefix"],
                                   self.public_website.app)
        else:
            self.public_website = None

    def prepare_stop(self) -> None:
        self.periodic_reconnect_task.cancel()
        self.log.debug("Stopping puppet syncers")
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.log.debug("Stopping facebook listeners")
        User.shutdown = True
        for user in User.by_fbid.values():
            user.stop_listen()

    async def stop(self) -> None:
        await super().stop()
        self.log.debug("Saving user sessions")
        for user in User.by_mxid.values():
            await user.save()

    async def start(self) -> None:
        await self.db.start()
        await self.state_store.upgrade_table.upgrade(self.db.pool)
        if self.matrix.e2ee:
            self.matrix.e2ee.crypto_db.override_pool(self.db.pool)
        self.add_startup_actions(User.init_cls(self))
        self.add_startup_actions(Puppet.init_cls(self))
        Portal.init_cls(self)
        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())
        await super().start()
        if self.public_website:
            self.public_website.ready_wait.set_result(None)
        self.periodic_reconnect_task = asyncio.create_task(self._try_periodic_reconnect_loop())

    async def resend_bridge_info(self) -> None:
        self.config["bridge.resend_bridge_info"] = False
        self.config.save()
        self.log.info("Re-sending bridge info state event to all portals")
        async for portal in Portal.all():
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
                        await user.reconnect()
                except asyncio.CancelledError:
                    log.debug("Periodic reconnect loop stopped")
                    return
                except Exception:
                    log.exception("Error while reconnecting", user.mxid)

    async def get_portal(self, room_id: RoomID) -> Portal:
        return await Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID, create: bool = False) -> Puppet:
        return await Puppet.get_by_mxid(user_id, create=create)

    async def get_double_puppet(self, user_id: UserID) -> Puppet:
        return await Puppet.get_by_custom_mxid(user_id)

    async def get_user(self, user_id: UserID, create: bool = True) -> User:
        return await User.get_by_mxid(user_id, create=create)

    def is_bridge_ghost(self, user_id: UserID) -> bool:
        return bool(Puppet.get_id_from_mxid(user_id))

    async def count_logged_in_users(self) -> int:
        return len([user for user in User.by_fbid.values() if user.fbid])


MessengerBridge().run()
