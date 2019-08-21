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
from typing import Iterable, List
import asyncio

from fbchat.models import User, ThreadLocation

from .. import puppet as pu, portal as po, user as u
from ..db import UserPortal as DBUserPortal
from . import command_handler, CommandEvent, SECTION_MISC


@command_handler(needs_auth=True, management_only=False,
                 help_section=SECTION_MISC, help_text="Search for a Facebook user",
                 help_args="<_search query_>")
async def search(evt: CommandEvent) -> None:
    res = await evt.sender.searchForUsers(" ".join(evt.args))
    await evt.reply(await _handle_search_result(evt.sender, res))


@command_handler(needs_auth=True, management_only=False)
async def search_by_id(evt: CommandEvent) -> None:
    res = await evt.sender.fetchUserInfo(*evt.args)
    await evt.reply(await _handle_search_result(evt.sender, res.values()))


async def _handle_search_result(sender: 'u.User', res: Iterable[User]) -> str:
    puppets: List[pu.Puppet] = await asyncio.gather(*[pu.Puppet.get_by_fbid(user.uid, create=True)
                                                    .update_info(sender, user)
                                                      for user in res])
    results = "".join(
        f"* [{puppet.name}](https://matrix.to/#/{puppet.default_mxid})\n"
        for puppet in puppets)
    if results:
        return f"Search results:\n\n{results}"
    else:
        return "No results :("


@command_handler(needs_auth=True, management_only=False, help_section=SECTION_MISC,
                 help_text="Synchronize portals", help_args="[_limit_] [--create] [--contacts]")
async def sync(evt: CommandEvent) -> None:
    contacts = False
    create_portals = False
    limit = 0
    for arg in evt.args:
        arg = arg.lower()
        if arg == "--contacts":
            contacts = True
        elif arg == "--create":
            create_portals = True
        else:
            limit = int(arg)

    threads = await evt.sender.fetchThreads(limit=limit, thread_location=ThreadLocation.INBOX)
    ups = DBUserPortal.all(evt.sender.fbid)
    for thread in threads:
        portal = po.Portal.get_by_thread(thread, evt.sender.fbid)
        if create_portals:
            await portal.create_matrix_room(evt.sender, thread)
        else:
            await evt.sender._add_community(ups.get(portal.fbid, None), None, portal, None)

    if contacts:
        await evt.sender.sync_contacts()

    await evt.reply("Syncing complete")
