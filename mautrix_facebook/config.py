# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2023 Tulir Asokan
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
import os

from mautrix.bridge.config import BaseBridgeConfig
from mautrix.types import UserID
from mautrix.util.config import ConfigUpdateHelper, ForbiddenDefault, ForbiddenKey


class Config(BaseBridgeConfig):
    @property
    def forbidden_defaults(self) -> list[ForbiddenDefault]:
        return [
            *super().forbidden_defaults,
            ForbiddenDefault("appservice.database", "postgres://username:password@hostname/db"),
            ForbiddenDefault(
                "appservice.public.external",
                "https://example.com/public",
                condition="appservice.public.enabled",
            ),
            ForbiddenDefault("bridge.permissions", ForbiddenKey("example.com")),
        ]

    def do_update(self, helper: ConfigUpdateHelper) -> None:
        super().do_update(helper)

        copy, copy_dict, base = helper

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
        copy("appservice.public.segment_key")
        copy("appservice.public.segment_user_id")

        copy("metrics.enabled")
        copy("metrics.listen_port")

        copy("bridge.username_template")
        copy("bridge.displayname_template")
        copy("bridge.displayname_preference")
        copy("bridge.command_prefix")

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
        copy("bridge.presence_from_facebook")
        copy("bridge.update_avatar_initial_sync")
        copy("bridge.delivery_receipts")
        copy("bridge.delivery_error_reports")
        copy("bridge.message_status_events")
        copy("bridge.federate_rooms")
        copy("bridge.allow_invites")
        copy("bridge.backfill.enable")
        copy("bridge.backfill.msc2716")
        copy("bridge.backfill.double_puppet_backfill")
        if "bridge.initial_chat_sync" in self:
            initial_chat_sync = self["bridge.initial_chat_sync"]
            base["bridge.backfill.max_conversations"] = self.get(
                "bridge.backfill.max_conversations", initial_chat_sync
            )
        else:
            copy("bridge.backfill.max_conversations")
        copy("bridge.backfill.min_sync_thread_delay")
        copy("bridge.backfill.unread_hours_threshold")
        copy("bridge.backfill.backoff.thread_list")
        copy("bridge.backfill.backoff.message_history")
        copy("bridge.backfill.incremental.max_pages")
        copy("bridge.backfill.incremental.max_total_pages")
        copy("bridge.backfill.incremental.page_delay")
        copy("bridge.backfill.incremental.post_batch_delay")
        if "bridge.periodic_reconnect_interval" in self:
            base["bridge.periodic_reconnect.interval"] = self["bridge.periodic_reconnect_interval"]
            base["bridge.periodic_reconnect.mode"] = self["bridge.periodic_reconnect_mode"]
        else:
            copy("bridge.periodic_reconnect.interval")
            copy("bridge.periodic_reconnect.mode")
            copy("bridge.periodic_reconnect.always")
            copy("bridge.periodic_reconnect.min_connected_time")
        copy("bridge.resync_max_disconnected_time")
        copy("bridge.max_startup_thread_sync_count")
        copy("bridge.temporary_disconnect_notices")
        copy("bridge.disable_bridge_notices")
        copy("bridge.bridge_matrix_notices")
        if "bridge.refresh_on_reconnection_fail" in self:
            base["bridge.on_reconnection_fail.action"] = (
                "refresh" if self["bridge.refresh_on_reconnection_fail"] else None
            )
            base["bridge.on_reconnection_fail.wait_for"] = 0
        elif "bridge.on_reconnection_fail.refresh" in self:
            base["bridge.on_reconnection_fail.action"] = (
                "refresh" if self["bridge.on_reconnection_fail.refresh"] else None
            )
            copy("bridge.on_reconnection_fail.wait_for")
        else:
            copy("bridge.on_reconnection_fail.action")
            copy("bridge.on_reconnection_fail.wait_for")
        copy("bridge.resend_bridge_info")
        copy("bridge.mute_bridging")
        copy("bridge.tag_only_on_create")
        copy("bridge.sandbox_media_download")

        copy_dict("bridge.permissions")

        copy("bridge.get_proxy_api_url")
        copy("bridge.private_chat_portal_meta")
        if base["bridge.private_chat_portal_meta"] not in ("default", "always", "never"):
            base["bridge.private_chat_portal_meta"] = "default"
        copy("bridge.disable_reply_fallbacks")

        for key in (
            "bridge.periodic_reconnect.interval",
            "bridge.on_reconnection_fail.wait_for",
        ):
            value = base.get(key, None)
            if isinstance(value, list) and len(value) != 2:
                raise ValueError(f"{key} must only be a list of two items")

        copy("facebook.device_seed")
        if base["facebook.device_seed"] == "generate":
            base["facebook.device_seed"] = self._new_token()
        copy("facebook.default_region_hint")
        copy("facebook.connection_type")
        copy("facebook.carrier")
        copy("facebook.hni")

    def _get_permissions(self, key: str) -> tuple[bool, bool, bool, str]:
        level = self["bridge.permissions"].get(key, "")
        admin = level == "admin"
        user = level == "user" or admin
        relay = level == "relay" or user
        return relay, user, admin, level

    def get_permissions(self, mxid: UserID) -> tuple[bool, bool, bool, str]:
        permissions = self["bridge.permissions"] or {}
        if mxid in permissions:
            return self._get_permissions(mxid)

        homeserver = mxid[mxid.index(":") + 1 :]
        if homeserver in permissions:
            return self._get_permissions(homeserver)

        return self._get_permissions("*")
