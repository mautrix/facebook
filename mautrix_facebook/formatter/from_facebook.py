# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from typing import Match
from html import escape
import re

from maufbapi.types import graphql, mqtt
from mautrix.types import Format, MessageType, TextMessageEventContent
from mautrix.util import utf16_surrogate

from .. import puppet as pu, user as u

_START = r"^|\s"
_END = r"$|\s"
_TEXT_NO_SURROUNDING_SPACE = r"(?:[^\s].*?[^\s])|[^\s]"
COMMON_REGEX = re.compile(rf"({_START})([_~*])({_TEXT_NO_SURROUNDING_SPACE})\2({_END})")
INLINE_CODE_REGEX = re.compile(rf"({_START})(`)(.+?)`({_END})")
MENTION_REGEX = re.compile(r"@([0-9]{1,15})\u2063(.+?)\u2063")

tags = {"_": "em", "*": "strong", "~": "del", "`": "code"}


def _handle_match(html: str, match: Match, nested: bool) -> tuple[str, int]:
    start, end = match.start(), match.end()
    prefix, sigil, text, suffix = match.groups()
    if nested:
        text = _convert_formatting(text)
    tag = tags[sigil]
    # We don't want to include the whitespace suffix length, as that could be used as the
    # whitespace prefix right after this formatting block.
    pos = start + len(prefix) + (2 * len(tag) + 5) + len(text)
    html = f"{html[:start]}{prefix}<{tag}>{text}</{tag}>{suffix}{html[end:]}"
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


def _handle_blockquote(output: list[str], blockquote: bool, line: str) -> tuple[bool, str]:
    if not blockquote and line.startswith("&gt; "):
        line = line[len("&gt; ") :]
        output.append("<blockquote>")
        blockquote = True
    elif blockquote:
        if line.startswith("&gt;"):
            line = line[len("&gt;") :]
            if line.startswith(" "):
                line = line[1:]
        else:
            output.append("</blockquote>")
            blockquote = False
    return blockquote, line


def _handle_codeblock_pre(
    output: list[str], codeblock: bool, line: str
) -> tuple[bool, str, tuple[str | None, str | None, str | None]]:
    cb = line.find("```")
    cb_lang = None
    cb_content = None
    post_cb_content = None
    if cb != -1:
        if not codeblock:
            cb_lang = line[cb + 3 :]
            if "```" in cb_lang:
                end = cb_lang.index("```")
                cb_content = cb_lang[:end]
                post_cb_content = cb_lang[end + 3 :]
                cb_lang = ""
            else:
                codeblock = True
            line = line[:cb]
        else:
            output.append("</code></pre>")
            codeblock = False
            line = line[cb + 3 :]
    return codeblock, line, (cb_lang, cb_content, post_cb_content)


def _handle_codeblock_post(
    output: list[str], cb_lang: str | None, cb_content: str | None, post_cb_content: str | None
) -> None:
    if cb_lang is not None:
        if cb_lang:
            output.append(f'<pre><code class="language-{cb_lang}">')
        else:
            output.append("<pre><code>")
        if cb_content:
            output.append(cb_content)
            output.append("</code></pre>")
            output.append(_convert_formatting(post_cb_content))


async def facebook_to_matrix(msg: graphql.MessageText | mqtt.Message) -> TextMessageEventContent:
    if isinstance(msg, mqtt.Message):
        text = msg.text
        mentions = msg.mentions
    elif isinstance(msg, graphql.MessageText):
        text = msg.text
        mentions = msg.ranges
    else:
        raise NotImplementedError(f"Unsupported Facebook message type {type(msg).__name__}")
    text = text or ""
    content = TextMessageEventContent(msgtype=MessageType.TEXT, body=text)

    text = utf16_surrogate.add(text)
    mention_user_ids = []
    for m in reversed(mentions):
        if isinstance(m, mqtt.Mention) and m.type != mqtt.MentionType.PERSON:
            continue
        original = text[m.offset : m.offset + m.length]
        if len(original) > 0 and original[0] == "@":
            original = original[1:]
        mention_user_ids.append(int(m.user_id))
        text = f"{text[:m.offset]}@{m.user_id}\u2063{original}\u2063{text[m.offset + m.length:]}"
    text = utf16_surrogate.remove(text)

    html = escape(text)
    output = []
    if html:
        codeblock = False
        blockquote = False
        line: str
        lines = html.split("\n")
        for i, line in enumerate(lines):
            blockquote, line = _handle_blockquote(output, blockquote, line)
            codeblock, line, post_args = _handle_codeblock_pre(output, codeblock, line)
            output.append(_convert_formatting(line))
            if i != len(lines) - 1:
                if codeblock:
                    output.append("\n")
                else:
                    output.append("<br/>")
            _handle_codeblock_post(output, *post_args)
    html = "".join(output)

    mention_user_map = {}
    for fbid in mention_user_ids:
        user = await u.User.get_by_fbid(fbid)
        if user:
            mention_user_map[fbid] = user.mxid
        else:
            puppet = await pu.Puppet.get_by_fbid(fbid, create=False)
            mention_user_map[fbid] = puppet.mxid if puppet else None

    def _mention_replacer(match: Match) -> str:
        mxid = mention_user_map[int(match.group(1))]
        if not mxid:
            return match.group(2)
        return f'<a href="https://matrix.to/#/{mxid}">{match.group(2)}</a>'

    html = MENTION_REGEX.sub(_mention_replacer, html)
    if html != escape(content.body).replace("\n", "<br/>\n"):
        content.format = Format.HTML
        content.formatted_body = html
    return content
