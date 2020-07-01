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
from typing import Tuple, List, Optional, Match
from html import escape
import re

import fbchat
from mautrix.types import TextMessageEventContent, Format, MessageType

from .. import puppet as pu, user as u

_START = r"^|\s"
_END = r"$|\s"
_TEXT_NO_SURROUNDING_SPACE = r"(?:[^\s].*?[^\s])|[^\s]"
COMMON_REGEX = re.compile(rf"({_START})([_~*])({_TEXT_NO_SURROUNDING_SPACE})\2({_END})")
INLINE_CODE_REGEX = re.compile(rf"({_START})(`)(.+?)`({_END})")
MENTION_REGEX = re.compile(r"@([0-9]{1,15})\u2063(.+)\u2063")

tags = {
    "_": "em",
    "*": "strong",
    "~": "del",
    "`": "code"
}


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


def _handle_blockquote(output: List[str], blockquote: bool, line: str) -> Tuple[bool, str]:
    if not blockquote and line.startswith("&gt; "):
        line = line[len("&gt; "):]
        output.append("<blockquote>")
        blockquote = True
    elif blockquote:
        if line.startswith("&gt;"):
            line = line[len("&gt;"):]
            if line.startswith(" "):
                line = line[1:]
        else:
            output.append("</blockquote>")
            blockquote = False
    return blockquote, line


OptStr = Optional[str]


def _handle_codeblock_pre(output: List[str], codeblock: bool, line: str
                          ) -> Tuple[bool, str, Tuple[OptStr, OptStr, OptStr]]:
    cb = line.find("```")
    cb_lang = None
    cb_content = None
    post_cb_content = None
    if cb != -1:
        if not codeblock:
            cb_lang = line[cb + 3:]
            if "```" in cb_lang:
                end = cb_lang.index("```")
                cb_content = cb_lang[:end]
                post_cb_content = cb_lang[end + 3:]
                cb_lang = ""
            else:
                codeblock = True
            line = line[:cb]
        else:
            output.append("</code></pre>")
            codeblock = False
            line = line[cb + 3:]
    return codeblock, line, (cb_lang, cb_content, post_cb_content)


def _handle_codeblock_post(output: List[str], cb_lang: OptStr, cb_content: OptStr,
                           post_cb_content: OptStr) -> None:
    if cb_lang is not None:
        if cb_lang:
            output.append("<pre><code>")
        else:
            output.append(f"<pre><code class=\"{cb_lang}\">")
        if cb_content:
            output.append(cb_content)
            output.append("</code></pre>")
            output.append(_convert_formatting(post_cb_content))


def facebook_to_matrix(message: fbchat.MessageData) -> TextMessageEventContent:
    text = message.text or ""
    content = TextMessageEventContent(msgtype=MessageType.TEXT, body=text)
    for m in reversed(message.mentions):
        original = text[m.offset:m.offset + m.length]
        if len(original) > 0 and original[0] == "@":
            original = original[1:]
        text = f"{text[:m.offset]}@{m.thread_id}\u2063{original}\u2063{text[m.offset + m.length:]}"
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
                output.append("<br/>")
            _handle_codeblock_post(output, *post_args)
    for attachment in message.attachments:
        if ((isinstance(attachment, fbchat.ShareAttachment)
             and attachment.original_url.rstrip("/") not in text)):
            output.append(f"<br/><a href='{attachment.original_url}'>"
                          f"{attachment.title or attachment.original_url}"
                          "</a>")
            content.body += (f"\n{attachment.title}: {attachment.original_url}"
                             if attachment.title else attachment.original_url)
    html = "".join(output)

    html = MENTION_REGEX.sub(_mention_replacer, html)
    if html != escape(content.body).replace("\n", "<br/>"):
        content.format = Format.HTML
        content.formatted_body = html
    return content
