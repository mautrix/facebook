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
from typing import List
import asyncio

from .. import puppet as pu
from . import command_handler, CommandEvent, SECTION_MISC


@command_handler(needs_auth=True, management_only=False,
                 help_section=SECTION_MISC, help_text="Search for a Facebook user",
                 help_args="<_search query_>")
async def search(evt: CommandEvent) -> None:
    res = await evt.sender.searchForUsers(" ".join(evt.args))
    puppets: List[pu.Puppet] = await asyncio.gather(*[pu.Puppet.get_by_fbid(user.uid, create=True)
                                                    .update_info(evt.sender, user)
                                                      for user in res])
    results = "".join(
        f"* [{puppet.name}](https://matrix.to/#/{puppet.default_mxid})\n"
        for puppet in puppets)
    if results:
        await evt.reply(f"Search results:\n\n{results}")
    else:
        await evt.reply("No results :(")
