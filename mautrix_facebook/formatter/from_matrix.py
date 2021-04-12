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
from typing import cast, NamedTuple, List

from mautrix.types import TextMessageEventContent, Format, RoomID, RelationType
from mautrix.util.formatter import (MatrixParser as BaseMatrixParser, MarkdownString, EntityString,
                                    SimpleEntity, EntityType)
from mautrix.util.logging import TraceLogger
from maufbapi.types.mqtt import Mention

from .. import puppet as pu, user as u
from ..db import Message as DBMessage


class SendParams(NamedTuple):
    text: str
    mentions: List[Mention]
    reply_to: str


class FacebookFormatString(EntityString[SimpleEntity, EntityType], MarkdownString):
    def format(self, entity_type: EntityType, **kwargs) -> 'FacebookFormatString':
        prefix = suffix = ""
        if entity_type == EntityType.USER_MENTION:
            self.entities.append(SimpleEntity(type=entity_type, offset=0, length=len(self.text),
                                              extra_info={"user_id": kwargs["user_id"]}))
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


async def matrix_to_facebook(content: TextMessageEventContent, room_id: RoomID, log: TraceLogger
                             ) -> SendParams:
    mentions = []
    reply_to = None
    if content.relates_to.rel_type == RelationType.REPLY:
        message = await DBMessage.get_by_mxid(content.relates_to.event_id, room_id)
        if message:
            content.trim_reply_fallback()
            reply_to = message.fbid
        else:
            log.warning(f"Couldn't find reply target {content.relates_to.event_id}"
                        " to bridge text message reply metadata to Facebook")
    if content.format == Format.HTML and content.formatted_body:
        parsed = MatrixParser.parse(content.formatted_body)
        text = parsed.text
        mentions = []
        for mention in parsed.entities:
            mxid = mention.extra_info["user_id"]
            user = await u.User.get_by_mxid(mxid, create=False)
            if user and user.fbid:
                fbid = user.fbid
            else:
                puppet = await pu.Puppet.get_by_mxid(mxid, create=False)
                if puppet:
                    fbid = puppet.fbid
                else:
                    continue
            mentions.append(Mention(user_id=str(fbid), offset=mention.offset,
                                    length=mention.length))
    else:
        text = content.body
    return SendParams(text=text, mentions=mentions, reply_to=reply_to)
