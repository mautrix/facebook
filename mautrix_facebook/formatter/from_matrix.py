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
from typing import Optional
from enum import Enum
import re

from fbchat.models import Message, Mention

from mautrix.types import TextMessageEventContent, Format, UserID, RoomID, RelationType
from mautrix.util.formatter import MatrixParser as BaseMatrixParser, FormattedString

from .. import puppet as pu, user as u
from ..db import Message as DBMessage


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


MENTION_REGEX = re.compile(r"@([0-9]{15})\u2063(.+)\u2063")


class MatrixParser(BaseMatrixParser):
    e = EntityType

    @classmethod
    def user_pill_to_fstring(cls, msg: FormattedString, user_id: UserID
                             ) -> Optional[FormattedString]:
        user = u.User.get_by_mxid(user_id, create=False)
        if user and user.fbid:
            return FormattedString(f"@{user.fbid}\u2063{msg.text}\u2063")
        puppet = pu.Puppet.get_by_mxid(user_id, create=False)
        if puppet:
            return FormattedString(f"@{puppet.fbid}\u2063{puppet.name or msg.text}\u2063")
        return msg


def matrix_to_facebook(content: TextMessageEventContent, room_id: RoomID) -> Message:
    mentions = []
    reply_to_id = None
    if content.relates_to.rel_type == RelationType.REFERENCE:
        message = DBMessage.get_by_mxid(content.relates_to.event_id, room_id)
        if message:
            content.trim_reply_fallback()
            reply_to_id = message.fbid
    if content.format == Format.HTML and content.formatted_body:
        text = MatrixParser.parse(content.formatted_body).text
        for mention in MENTION_REGEX.finditer(text):
            fbid, name = mention.groups()
            start, end = mention.start(), mention.end()
            text = f"{text[:start]}{name}{text[end:]}"
            mentions.append(Mention(thread_id=fbid, offset=start, length=len(name)))
    else:
        text = content.body
    return Message(text=text, mentions=mentions, reply_to_id=reply_to_id)
