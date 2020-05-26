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
from typing import Awaitable, Callable, Dict, Optional, NamedTuple

from mautrix.bridge.commands import (HelpSection, CommandEvent as BaseCommandEvent,
                                     command_handler as base_command_handler,
                                     CommandHandler as BaseCommandHandler,
                                     CommandProcessor as BaseCommandProcessor)
from .. import user as u, context as c

HelpCacheKey = NamedTuple('FBHelpCacheKey', is_management=bool, is_admin=bool, is_logged_in=bool)

SECTION_AUTH = HelpSection("Authentication", 10, "")
SECTION_CONNECTION = HelpSection("Connection management", 15, "")
SECTION_CREATING_PORTALS = HelpSection("Creating portals", 20, "")
SECTION_PORTAL_MANAGEMENT = HelpSection("Portal management", 30, "")
SECTION_MISC = HelpSection("Miscellaneous", 40, "")
SECTION_ADMIN = HelpSection("Administration", 50, "")


class CommandEvent(BaseCommandEvent):
    sender: 'u.User'

    @property
    def print_error_traceback(self) -> bool:
        return self.sender.is_admin

    async def get_help_key(self) -> HelpCacheKey:
        return HelpCacheKey(is_management=self.is_management,
                            is_admin=self.sender.is_admin,
                            is_logged_in=await self.sender.is_logged_in())


class CommandHandler(BaseCommandHandler):
    needs_auth: bool
    needs_admin: bool

    async def get_permission_error(self, evt: CommandEvent) -> Optional[str]:
        err = await super().get_permission_error(evt)
        if err:
            return err
        elif self.needs_admin and not evt.sender.is_admin:
            return "This command requires administrator privileges."
        elif self.needs_auth and not await evt.sender.is_logged_in():
            return "This command requires you to be logged in."
        return None

    def has_permission(self, key: HelpCacheKey) -> bool:
        return ((not self.management_only or key.is_management) and
                (not self.needs_admin or key.is_admin) and
                (not self.needs_auth or key.is_logged_in))


def command_handler(_func: Optional[Callable[[CommandEvent], Awaitable[Dict]]] = None, *,
                    needs_auth: bool = True, needs_admin: bool = False,
                    management_only: bool = False, name: Optional[str] = None,
                    help_text: str = "", help_args: str = "", help_section: HelpSection = None
                    ) -> Callable[[Callable[[CommandEvent], Awaitable[Optional[Dict]]]],
                                  CommandHandler]:
    return base_command_handler(_func, management_only=management_only, name=name,
                                help_text=help_text, help_args=help_args, help_section=help_section,
                                needs_auth=needs_auth, needs_admin=needs_admin,
                                _handler_class=CommandHandler)


class CommandProcessor(BaseCommandProcessor):
    def __init__(self, context: c.Context) -> None:
        super().__init__(az=context.az, config=context.config, event_class=CommandEvent,
                         loop=context.loop, bridge=context.bridge)
