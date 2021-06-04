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
from typing import Any, Tuple, List
import os

from mautrix.types import UserID
from mautrix.util.config import ConfigUpdateHelper, ForbiddenDefault, ForbiddenKey
from mautrix.bridge.config import BaseBridgeConfig


class Config(BaseBridgeConfig):
    def __getitem__(self, key: str) -> Any:
        try:
            return os.environ[f"MAUTRIX_FACEBOOK_{key.replace('.', '_').upper()}"]
        except KeyError:
            return super().__getitem__(key)

    @property
    def forbidden_defaults(self) -> List[ForbiddenDefault]:
        return [
            *super().forbidden_defaults,
            ForbiddenDefault("appservice.database", "postgres://username:password@hostname/db"),
            ForbiddenDefault("appservice.public.external", "https://example.com/public",
                             condition="appservice.public.enabled"),
            ForbiddenDefault("bridge.permissions", ForbiddenKey("example.com"))
        ]

    def do_update(self, helper: ConfigUpdateHelper) -> None:
        super().do_update(helper)

        copy, copy_dict, base = helper

        copy("homeserver.asmux")

        copy("appservice.community_id")

        if self["appservice.bot_avatar"] == "mxc://maunium.net/ddtNPZSKMNqaUzqrHuWvUADv":
            base["appservice.bot_avatar"] = "mxc://maunium.net/ygtkteZsXnGJLJHRchUwYWak"

        copy("appservice.public.enabled")
        copy("appservice.public.prefix")
        copy("appservice.public.external")
        if self["appservice.public.shared_secret"] == "generate":
            base["appservice.public.shared_secret"] = self._new_token()
        else:
            copy("appservice.public.shared_secret")
        copy("appservice.public.allow_matrix_login")

        copy("metrics.enabled")
        copy("metrics.listen_port")

        copy("bridge.username_template")
        copy("bridge.displayname_template")
        copy("bridge.displayname_preference")
        copy("bridge.community_template")
        copy("bridge.command_prefix")

        copy("bridge.initial_chat_sync")
        copy("bridge.invite_own_puppet_to_pm")
        copy("bridge.sync_with_custom_puppets")
        copy("bridge.sync_direct_chat_list")
        copy("bridge.double_puppet_server_map")
        copy("bridge.double_puppet_allow_discovery")
        if "bridge.login_shared_secret" in self:
            base["bridge.login_shared_secret_map"] = {
                base["homeserver.domain"]: self["bridge.login_shared_secret"]
            }
        else:
            copy("bridge.login_shared_secret_map")
        copy("bridge.presence")
        copy("bridge.update_avatar_initial_sync")
        copy("bridge.encryption.allow")
        copy("bridge.encryption.default")
        copy("bridge.encryption.key_sharing.allow")
        copy("bridge.encryption.key_sharing.require_cross_signing")
        copy("bridge.encryption.key_sharing.require_verification")
        copy("bridge.delivery_receipts")
        copy("bridge.allow_invites")
        copy("bridge.backfill.invite_own_puppet")
        copy("bridge.backfill.initial_limit")
        copy("bridge.backfill.missed_limit")
        copy("bridge.backfill.disable_notifications")
        if "bridge.periodic_reconnect_interval" in self:
            base["bridge.periodic_reconnect.interval"] = self["bridge.periodic_reconnect_interval"]
            base["bridge.periodic_reconnect.mode"] = self["bridge.periodic_reconnect_mode"]
        else:
            copy("bridge.periodic_reconnect.interval")
            copy("bridge.periodic_reconnect.mode")
            copy("bridge.periodic_reconnect.always")
        copy("bridge.resync_max_disconnected_time")
        copy("bridge.temporary_disconnect_notices")
        copy("bridge.disable_bridge_notices")
        copy("bridge.refresh_on_reconnection_fail")
        copy("bridge.resend_bridge_info")
        copy("bridge.mute_bridging")
        copy("bridge.tag_only_on_create")

        copy_dict("bridge.permissions")

    def _get_permissions(self, key: str) -> Tuple[bool, bool, str]:
        level = self["bridge.permissions"].get(key, "")
        admin = level == "admin"
        user = level == "user" or admin
        return user, admin, level

    def get_permissions(self, mxid: UserID) -> Tuple[bool, bool, str]:
        permissions = self["bridge.permissions"] or {}
        if mxid in permissions:
            return self._get_permissions(mxid)

        homeserver = mxid[mxid.index(":") + 1:]
        if homeserver in permissions:
            return self._get_permissions(homeserver)

        return self._get_permissions("*")
