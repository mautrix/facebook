# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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

from typing import Any
import asyncio
import logging
import time

from mautrix.bridge import Bridge
from mautrix.types import RoomID, UserID

from .config import Config
from .db import init as init_db, upgrade_table
from .matrix import MatrixHandler
from .portal import Portal
from .presence import PresenceUpdater
from .puppet import Puppet
from .user import User
from .util.interval import get_interval
from .version import linkified_version, version
from .web import PublicBridgeWebsite


class MessengerBridge(Bridge):
    name = "mautrix-facebook"
    module = "mautrix_facebook"
    command = "python -m mautrix-facebook"
    description = "A Matrix-Facebook Messenger puppeting bridge."
    repo_url = "https://github.com/mautrix/facebook"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler
    upgrade_table = upgrade_table

    config: Config
    matrix: MatrixHandler
    public_website: PublicBridgeWebsite | None

    periodic_reconnect_task: asyncio.Task | None
    periodic_presence_task: asyncio.Task | None

    def prepare_db(self) -> None:
        super().prepare_db()
        init_db(self.db)

    def prepare_bridge(self) -> None:
        super().prepare_bridge()
        if self.config["appservice.public.enabled"]:
            secret = self.config["appservice.public.shared_secret"]
            segment_key = self.config["appservice.public.segment_key"]
            self.public_website = PublicBridgeWebsite(
                loop=self.loop,
                shared_secret=secret,
                segment_key=segment_key,
            )
            self.az.app.add_subapp(
                self.config["appservice.public.prefix"], self.public_website.app
            )
        else:
            self.public_website = None
        self.periodic_reconnect_task = None
        self.periodic_presence_task = None

    def prepare_stop(self) -> None:
        if self.periodic_reconnect_task:
            self.periodic_reconnect_task.cancel()
        if self.periodic_presence_task:
            self.periodic_presence_task.cancel()
        self.log.debug("Stopping puppet syncers")
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.log.debug("Stopping facebook listeners")
        User.shutdown = True
        for user in User.by_fbid.values():
            user.stop_listen()
        self.add_shutdown_actions(user.save() for user in User.by_mxid.values())

    async def start(self) -> None:
        self.add_startup_actions(User.init_cls(self))
        self.add_startup_actions(Puppet.init_cls(self))
        Portal.init_cls(self)
        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())
        await super().start()
        if self.public_website:
            self.public_website.ready_wait.set_result(None)
        self.periodic_reconnect_task = asyncio.create_task(self._try_periodic_reconnect_loop())
        if self.config["bridge.presence_from_facebook"]:
            self.periodic_presence_task = asyncio.create_task(
                PresenceUpdater.refresh_periodically()
            )

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
        interval = get_interval(self.config["bridge.periodic_reconnect.interval"])
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
            must_be_connected_before = time.monotonic()
            min_connected_time = self.config["bridge.periodic_reconnect.min_connected_time"]
            if min_connected_time:
                must_be_connected_before -= min_connected_time
            log.info("Executing periodic reconnections")
            for user in User.by_fbid.values():
                if not user.is_connected and not always_reconnect:
                    log.debug("Not reconnecting %s: not connected", user.mxid)
                    continue
                if user.is_connected and user.connection_time >= must_be_connected_before:
                    log.debug("No reconnecting %s: connected too recently", user.mxid)
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
                    log.exception("Error while reconnecting %s", user.mxid)

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

    async def manhole_global_namespace(self, user_id: UserID) -> dict[str, Any]:
        return {
            **await super().manhole_global_namespace(user_id),
            "User": User,
            "Portal": Portal,
            "Puppet": Puppet,
        }


MessengerBridge().run()
