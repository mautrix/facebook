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
from typing import Dict, Optional, Tuple
import random
import string

from mautrix.types import UserID
from mautrix.util.config import BaseFileConfig, ConfigUpdateHelper, yaml


class Config(BaseFileConfig):
    registration_path: str
    _registration: Optional[Dict]

    def __init__(self, path: str, registration_path: str, base_path: str) -> None:
        super().__init__(path, base_path)
        self.registration_path = registration_path
        self._registration = None

    def save(self) -> None:
        super().save()
        if self._registration and self.registration_path:
            with open(self.registration_path, 'w') as stream:
                yaml.dump(self._registration, stream)

    @staticmethod
    def _new_token() -> str:
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=64))

    def do_update(self, helper: ConfigUpdateHelper) -> None:
        base, copy, copy_dict = helper.base, helper.copy, helper.copy_dict

        copy("homeserver.address")
        copy("homeserver.domain")
        copy("homeserver.verify_ssl")

        copy("appservice.address")
        copy("appservice.hostname")
        copy("appservice.port")
        copy("appservice.max_body_size")

        copy("appservice.database")

        copy("appservice.id")
        copy("appservice.bot_username")
        copy("appservice.bot_displayname")
        copy("appservice.bot_avatar")

        copy("appservice.as_token")
        copy("appservice.hs_token")

        copy("bridge.username_template")

        copy("bridge.command_prefix")

        copy("bridge.initial_chat_sync")
        copy("bridge.invite_own_puppet_to_pm")
        copy("bridge.sync_with_custom_puppets")
        copy("bridge.presence")

        copy_dict("bridge.permissions")

        copy("logging")

    def _get_permissions(self, key: str) -> Tuple[bool, bool]:
        level = self["bridge.permissions"].get(key, "")
        admin = level == "admin"
        user = level == "user" or admin
        return user, admin

    def get_permissions(self, mxid: UserID) -> Tuple[bool, bool]:
        permissions = self["bridge.permissions"] or {}
        if mxid in permissions:
            return self._get_permissions(mxid)

        homeserver = mxid[mxid.index(":") + 1:]
        if homeserver in permissions:
            return self._get_permissions(homeserver)

        return self._get_permissions("*")

    def generate_registration(self) -> None:
        homeserver = self["homeserver.domain"]

        username_format = self["bridge.username_template"].lower().format(userid=".+")

        self.set("appservice.as_token", self._new_token())
        self.set("appservice.hs_token", self._new_token())

        self._registration = {
            "id": self["appservice.id"] or "facebook",
            "as_token": self["appservice.as_token"],
            "hs_token": self["appservice.hs_token"],
            "namespaces": {
                "users": [{
                    "exclusive": True,
                    "regex": f"@{username_format}:{homeserver}"
                }],
            },
            "url": self["appservice.address"],
            "sender_localpart": self["appservice.bot_username"],
            "rate_limited": False
        }
