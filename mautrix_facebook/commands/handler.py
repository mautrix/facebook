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
from typing import Awaitable, Callable, Dict, Optional, NamedTuple, List

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
SECTION_ADMIN = HelpSection("Administration", 50, "")
