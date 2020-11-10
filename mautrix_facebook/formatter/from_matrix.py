# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import Optional, Dict, Any, cast, TYPE_CHECKING

from fbchat import Mention

from mautrix.types import TextMessageEventContent, Format, UserID, RoomID, RelationType
from mautrix.util.formatter import (MatrixParser as BaseMatrixParser, MarkdownString, EntityString,
                                    SimpleEntity, EntityType)

from .. import puppet as pu, user as u
from ..db import Message as DBMessage


if TYPE_CHECKING:
    from typing import TypedDict, List

    class SendParams(TypedDict):
        text: str
        mentions: List[Mention]
        reply_to_id: str


class FacebookFormatString(EntityString[SimpleEntity, EntityType], MarkdownString):
    def _mention_to_entity(self, mxid: UserID) -> Optional[SimpleEntity]:
        user = u.User.get_by_mxid(mxid, create=False)
        if user and user.fbid:
            fbid = user.fbid
        else:
            puppet = pu.Puppet.deprecated_sync_get_by_mxid(mxid, create=False)
            if puppet:
                fbid = puppet.fbid
            else:
                return None
        return SimpleEntity(type=EntityType.USER_MENTION, offset=0, length=len(self.text),
                            extra_info={"user_id": mxid, "fbid": fbid})

    def format(self, entity_type: EntityType, **kwargs) -> 'FacebookFormatString':
        prefix = suffix = ""
        if entity_type == EntityType.USER_MENTION:
            mention = self._mention_to_entity(kwargs['user_id'])
            if mention:
                self.entities.append(mention)
            return self
        elif entity_type == EntityType.BOLD:
            prefix = suffix = "*"
        elif entity_type == EntityType.ITALIC:
            prefix = suffix = "_"
        elif entity_type == EntityType.STRIKETHROUGH:
            prefix = suffix = "~"
        elif entity_type == EntityType.URL:
            if kwargs['url'] != self.text:
                suffix = f" ({kwargs['url']})"
        elif entity_type == EntityType.PREFORMATTED:
            prefix = f"```{kwargs['language']}\n"
            suffix = "\n```"
        elif entity_type == EntityType.INLINE_CODE:
            prefix = suffix = "`"
        elif entity_type == EntityType.BLOCKQUOTE:
            children = self.trim().split("\n")
            children = [child.prepend("> ") for child in children]
            return self.join(children, "\n")
        elif entity_type == EntityType.HEADER:
            prefix = "#" * kwargs["size"] + " "
        else:
            return self

        self._offset_entities(len(prefix))
        self.text = f"{prefix}{self.text}{suffix}"
        return self


class MatrixParser(BaseMatrixParser[FacebookFormatString]):
    fs = FacebookFormatString

    @classmethod
    def parse(cls, data: str) -> FacebookFormatString:
        return cast(FacebookFormatString, super().parse(data))


def matrix_to_facebook(content: TextMessageEventContent, room_id: RoomID) -> 'SendParams':
    mentions = []
    reply_to_id = None
    if content.relates_to.rel_type == RelationType.REPLY:
        message = DBMessage.get_by_mxid(content.relates_to.event_id, room_id)
        if message:
            content.trim_reply_fallback()
            reply_to_id = message.fbid
    if content.format == Format.HTML and content.formatted_body:
        parsed = MatrixParser.parse(content.formatted_body)
        text = parsed.text
        mentions = [Mention(thread_id=mention.extra_info['fbid'], offset=mention.offset,
                            length=mention.length)
                    for mention in parsed.entities]
    else:
        text = content.body
    return {"text": text, "mentions": mentions, "reply_to_id": reply_to_id}
