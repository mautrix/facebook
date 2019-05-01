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
from logging import Formatter, LogRecord
from copy import copy

PREFIX = "\033["

LEVEL_COLORS = {
    "DEBUG": "37m",  # white
    "INFO": "36m",  # cyan
    "WARNING": "33;1m",  # yellow
    "ERROR": "31;1m",  # red
    "CRITICAL": "41m",  # white on red bg
}

FBCHAT_COLOR = PREFIX + "35;1m"  # magenta
MAU_COLOR = PREFIX + "32;1m"  # green
AIOHTTP_COLOR = PREFIX + "36;1m"  # cyan
MXID_COLOR = PREFIX + "33m"  # yellow

RESET = "\033[0m"


class ColorFormatter(Formatter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _color_name(self, module: str) -> str:
        fbchat = ["fbchat.util", "fbchat.request", "fbchat.client"]
        for prefix in fbchat:
            if module.startswith(prefix):
                return (FBCHAT_COLOR + prefix + RESET
                        + "." + MXID_COLOR + module[len(prefix) + 1:] + RESET)
        if module.startswith("mau.as"):
            return MAU_COLOR + module + RESET
        elif module.startswith("mau."):
            try:
                next_dot = module.index(".", len("mau."))
                return (MAU_COLOR + module[:next_dot] + RESET
                        + "." + MXID_COLOR + module[next_dot + 1:] + RESET)
            except ValueError:
                return MAU_COLOR + module + RESET
        elif module.startswith("aiohttp"):
            return AIOHTTP_COLOR + module + RESET

    def format(self, record: LogRecord):
        colored_record: LogRecord = copy(record)
        colored_record.name = self._color_name(record.name)
        try:
            levelname = record.levelname
            colored_record.levelname = PREFIX + LEVEL_COLORS[levelname] + levelname + RESET
        except KeyError:
            pass
        return super().format(colored_record)
