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
from typing import Tuple, Match
from html import escape
import re

from fbchat.models import Message

from mautrix.types import TextMessageEventContent, Format, MessageType

from .. import puppet as pu, user as u

_START = r"^|\s"
_END = r"$|\s"
_TEXT_NO_SURROUNDING_SPACE = r"(?:[^\s].*?[^\s])|[^\s]"
COMMON_REGEX = re.compile(rf"({_START})([_~*])({_TEXT_NO_SURROUNDING_SPACE})\2({_END})")
INLINE_CODE_REGEX = re.compile(rf"({_START})(`)(.+?)`({_END})")
CODE_BLOCK_REGEX = re.compile(r"(```.+```)")
MENTION_REGEX = re.compile(r"@([0-9]{15})\u2063(.+)\u2063")

tags = {
    "_": "em",
    "*": "strong",
    "~": "del",
    "`": "code"
}


def _code_block_replacer(code: str) -> str:
    if "\n" in code:
        lang, code = code.split("\n", 1)
        lang = lang.strip()
        if lang:
            return f"<pre><code class=\"language-{lang}\">{code}</code></pre>"
    return f"<pre><code>{code}</code></pre>"


def _mention_replacer(match: Match) -> str:
    fbid = match.group(1)

    user = u.User.get_by_fbid(fbid)
    if user:
        return f"<a href=\"https://matrix.to/#/{user.mxid}\">{match.group(2)}</a>"

    puppet = pu.Puppet.get_by_fbid(fbid, create=False)
    if puppet:
        return f"<a href=\"https://matrix.to/#/{puppet.mxid}\">{match.group(2)}</a>"


def _handle_match(html: str, match: Match, nested: bool) -> Tuple[str, int]:
    start, end = match.start(), match.end()
    prefix, sigil, text, suffix = match.groups()
    if nested:
        text = _convert_formatting(text)
    tag = tags[sigil]
    # We don't want to include the whitespace suffix length, as that could be used as the
    # whitespace prefix right after this formatting block.
    pos = start + len(prefix) + (2 * len(tag) + 5) + len(text)
    html = (f"{html[:start]}{prefix}"
            f"<{tag}>{text}</{tag}>"
            f"{suffix}{html[end:]}")
    return html, pos


def _convert_formatting(html: str) -> str:
    pos = 0
    while pos < len(html):
        i_match = INLINE_CODE_REGEX.search(html, pos)
        c_match = COMMON_REGEX.search(html, pos)
        if i_match and c_match:
            match = min(i_match, c_match, key=lambda match: match.start())
        else:
            match = i_match or c_match

        if match:
            html, pos = _handle_match(html, match, nested=match != i_match)
        else:
            break
    return html


def facebook_to_matrix(message: Message) -> TextMessageEventContent:
    content = TextMessageEventContent(msgtype=MessageType.TEXT, body=message.text)
    text = message.text
    for m in reversed(message.mentions):
        original = text[m.offset:m.offset + m.length]
        if len(original) > 0 and original[0] == "@":
            original = original[1:]
        text = f"{text[:m.offset]}@{m.thread_id}\u2063{original}\u2063{text[m.offset + m.length:]}"
    html = escape(text)
    html = "".join(_code_block_replacer(part[3:-3]) if part[:3] == "```" == part[-3:]
                   else _convert_formatting(part)
                   for part in CODE_BLOCK_REGEX.split(html))

    html = MENTION_REGEX.sub(_mention_replacer, html)
    if html != escape(content.body):
        content.format = Format.HTML
        content.formatted_body = html
    return content
