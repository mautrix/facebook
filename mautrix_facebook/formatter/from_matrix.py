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
from enum import Enum

from fbchat.models import Message

from mautrix.types import TextMessageEventContent, Format
from mautrix.util.formatter import MatrixParser as BaseMatrixParser


class EntityType(Enum):
    BOLD = 1
    ITALIC = 2
    STRIKETHROUGH = 3
    UNDERLINE = 4
    URL = 5
    INLINE_URL = 6
    EMAIL = 7
    PREFORMATTED = 8
    INLINE_CODE = 9

    def apply(self, text: str, **kwargs) -> str:
        if self == EntityType.BOLD:
            return f"*{text}*"
        elif self == EntityType.ITALIC:
            return f"_{text}_"
        elif self == EntityType.STRIKETHROUGH:
            return f"~{text}~"
        elif self == EntityType.INLINE_URL:
            return f"{text} ({kwargs['url']})"
        elif self == EntityType.PREFORMATTED:
            return f"```{kwargs['language']}\n{text}\n```"
        elif self == EntityType.INLINE_CODE:
            return f"`{text}`"
        return text


class MatrixParser(BaseMatrixParser):
    e = EntityType


def matrix_to_facebook(content: TextMessageEventContent) -> Message:
    if content.format == Format.HTML and content.formatted_body:
        text = MatrixParser.parse(content.formatted_body).text
    else:
        text = content.body
    return Message(text=text)
