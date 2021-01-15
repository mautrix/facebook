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
import asyncio
import time

from yarl import URL

from mautrix.client import Client
from mautrix.util.signed_token import sign_token
from mautrix.bridge.commands import HelpSection, command_handler
from mautrix.bridge import custom_puppet as cpu

from maufbapi import AndroidState, AndroidAPI

from .. import puppet as pu
from .typehint import CommandEvent

SECTION_AUTH = HelpSection("Authentication", 10, "")


@command_handler(needs_auth=False, management_only=True,
                 help_section=SECTION_AUTH, help_text="Log in to Facebook",
                 help_args="<_email_> <_password_>")
async def login(evt: CommandEvent) -> None:
    if len(evt.args) < 2:
        await evt.reply("Usage: `$cmdprefix+sp login <email> <password>`")
        return
    elif evt.sender.client:
        await evt.reply("You're already logged in")
        return
    evt.sender.command_status = {
        "action": "Login",
        "room_id": evt.room_id,
    }
    state = AndroidState()
    state.generate(evt.sender.mxid)
    api = AndroidAPI(state, log=evt.sender.log.getChild("login-api"))
    await evt.reply("Logging in...")
    try:
        await api.mobile_config_sessionless()
        await api.login(evt.args[0], " ".join(evt.args[1:]))
        await evt.sender.on_logged_in(state)
        evt.sender.command_status = None
        await evt.reply("Successfully logged in")
    # TODO more granular catching?
    except Exception as e:
        evt.sender.command_status = None
        await evt.reply(f"Failed to log in: {e}")
        evt.log.exception("Failed to log in")


async def enter_2fa_code(evt: CommandEvent) -> None:
    code = " ".join(evt.args)
    future: asyncio.Future = evt.sender.command_status["future"]
    future.set_result(code)
    del evt.sender.command_status["future"]
    del evt.sender.command_status["next"]


# TODO uncomment after fixing web login
# @command_handler(needs_auth=False, management_only=True,
#                  help_section=SECTION_AUTH, help_text="Log in to Facebook with Cookie Monster")
# async def login_web(evt: CommandEvent) -> None:
#     if evt.sender.client:
#         await evt.reply("You're already logged in")
#         return
#     external_url = URL(evt.config["appservice.public.external"])
#     token = sign_token(evt.processor.bridge.public_website.secret_key, {
#         "mxid": evt.sender.mxid,
#         "bridge_type": "net.maunium.facebook",
#         "login_api": str(external_url / "api" / "login"),
#         "homeserver": evt.az.domain,
#         "expiry": int(time.time()) + 30 * 60,
#     })
#     url = (external_url / "login.html").with_fragment(token)
#     await evt.reply(f"Visit [the login page]({url}) and follow the instructions")
#     evt.sender.command_status = {
#         "action": "Login",
#         "room_id": evt.room_id,
#     }


@command_handler(needs_auth=True, help_section=SECTION_AUTH, help_text="Log out of Facebook")
async def logout(evt: CommandEvent) -> None:
    puppet = await pu.Puppet.get_by_fbid(evt.sender.fbid)
    await evt.sender.logout()
    if puppet.is_real_user:
        await puppet.switch_mxid(None, None)
    await evt.reply("Successfully logged out")


@command_handler(needs_auth=True, management_only=True, help_args="<_access token_>",
                 help_section=SECTION_AUTH, help_text="Replace your Facebook Messenger account's "
                                                      "Matrix puppet with your Matrix account")
async def login_matrix(evt: CommandEvent) -> None:
    puppet = await pu.Puppet.get_by_fbid(evt.sender.fbid)
    _, homeserver = Client.parse_mxid(evt.sender.mxid)
    if homeserver != pu.Puppet.hs_domain:
        await evt.reply("You can't log in with an account on a different homeserver")
        return
    try:
        await puppet.switch_mxid(" ".join(evt.args), evt.sender.mxid)
        await evt.reply("Successfully replaced your Facebook Messenger account's "
                        "Matrix puppet with your Matrix account.")
    except cpu.OnlyLoginSelf:
        await evt.reply("You may only log in with your own Matrix account")
    except cpu.InvalidAccessToken:
        await evt.reply("Invalid access token")


@command_handler(needs_auth=True, management_only=True, help_section=SECTION_AUTH,
                 help_text="Revert your Facebook Messenger account's Matrix puppet to the original")
async def logout_matrix(evt: CommandEvent) -> None:
    puppet = await pu.Puppet.get_by_fbid(evt.sender.fbid)
    if not puppet.is_real_user:
        await evt.reply("You're not logged in with your Matrix account")
        return
    await puppet.switch_mxid(None, None)
    await evt.reply("Restored the original puppet for your Facebook Messenger account")
