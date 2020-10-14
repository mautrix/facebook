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
from typing import cast

import fbchat
from mautrix.bridge.commands import HelpSection, command_handler

from .typehint import CommandEvent

SECTION_CONNECTION = HelpSection("Connection management", 15, "")


@command_handler(needs_auth=False, management_only=True, help_section=SECTION_CONNECTION,
                 help_text="Mark this room as your bridge notice room")
async def set_notice_room(evt: CommandEvent) -> None:
    evt.sender.notice_room = evt.room_id
    evt.sender.save()
    await evt.reply("This room has been marked as your bridge notice room")


@command_handler(needs_auth=True, management_only=True, help_section=SECTION_CONNECTION,
                 help_text="Disconnect from Facebook Messenger")
async def disconnect(evt: CommandEvent) -> None:
    if not evt.sender.listener:
        await evt.reply("You don't have a Messenger MQTT connection")
        return
    evt.sender.listener.disconnect()


@command_handler(needs_auth=True, management_only=True, help_section=SECTION_CONNECTION,
                 help_text="Connect to Facebook Messenger", aliases=["reconnect"])
async def connect(evt: CommandEvent) -> None:
    if evt.sender.listen_task and not evt.sender.listen_task.done():
        await evt.reply("You already have a Messenger MQTT connection")
        return
    evt.sender.start_listen()


@command_handler(needs_auth=True, management_only=True, help_section=SECTION_CONNECTION,
                 help_text="Check if you're logged into Facebook Messenger")
async def ping(evt: CommandEvent) -> None:
    if not await evt.sender.is_logged_in():
        await evt.reply("You're not logged into Facebook Messenger")
        return
    try:
        own_info = cast(fbchat.User,
                        await evt.sender.client.fetch_thread_info([evt.sender.fbid]).__anext__())
    except fbchat.PleaseRefresh as e:
        await evt.reply(f"{e}\n\nUse `$cmdprefix+sp refresh` refresh the session.")
        return
    await evt.reply(f"You're logged in as {own_info.name} (user ID {own_info.id})")

    if not evt.sender.listen_task or evt.sender.listen_task.done():
        await evt.reply("You don't have a Messenger MQTT connection. Use `connect` to connect.")
    elif not evt.sender.is_connected:
        await evt.reply("The Messenger MQTT listener is **disconnected**.")
    else:
        await evt.reply("The Messenger MQTT listener is connected.")


@command_handler(needs_auth=True, management_only=True, help_section=SECTION_CONNECTION,
                 help_text="\"Refresh\" the Facebook Messenger page")
async def refresh(evt: CommandEvent) -> None:
    await evt.sender.refresh(force_notice=True)
