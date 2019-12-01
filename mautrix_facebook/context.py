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
from typing import Optional, Tuple, TYPE_CHECKING
from asyncio import AbstractEventLoop

from mautrix.appservice import AppService

from .config import Config

if TYPE_CHECKING:
    from .matrix import MatrixHandler
    from .__main__ import MessengerBridge


class Context:
    az: AppService
    config: Config
    loop: AbstractEventLoop
    mx: Optional['MatrixHandler']
    bridge: 'MessengerBridge'

    def __init__(self, az: AppService, config: Config, loop: AbstractEventLoop,
                 bridge: 'MessengerBridge') -> None:
        self.az = az
        self.config = config
        self.loop = loop
        self.mx = None
        self.bridge = bridge

    @property
    def core(self) -> Tuple[AppService, Config, AbstractEventLoop]:
        return self.az, self.config, self.loop
