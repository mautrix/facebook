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
import asyncio
from aiohttp.cookiejar import SimpleCookie

from fbchat.models import FBchatUserError
from mautrix.client import Client

from .. import puppet as pu
from mautrix.bridge import custom_puppet as cpu
from . import command_handler, CommandEvent, SECTION_AUTH


@command_handler(needs_auth=False, management_only=True,
                 help_section=SECTION_AUTH, help_text="Log in to Facebook",
                 help_args="<_email_> <_password_>")
async def login(evt: CommandEvent) -> None:
    if len(evt.args) < 2:
        await evt.reply("Usage: `$cmdprefix+sp login <email> <password>`")
        return
    evt.sender.command_status = {
        "action": "Login",
        "room_id": evt.room_id,
    }
    await evt.reply("Logging in...")
    try:
        await evt.sender.login(evt.args[0], " ".join(evt.args[1:]), max_tries=1)
        evt.sender.command_status = None
    except FBchatUserError as e:
        evt.sender.command_status = None
        await evt.reply(f"Failed to log in: {e}")
        evt.log.exception("Failed to log in")


async def enter_2fa_code(evt: CommandEvent) -> None:
    code = " ".join(evt.args)
    future: asyncio.Future = evt.sender.command_status["future"]
    future.set_result(code)
    del evt.sender.command_status["future"]
    del evt.sender.command_status["next"]


@command_handler(needs_auth=False, management_only=True,
                 help_section=SECTION_AUTH, help_text="Log in to Facebook manually")
async def login_cookie(evt: CommandEvent) -> None:
    evt.sender.command_status = {
        "action": "Login",
        "room_id": evt.room_id,
        "next": enter_login_cookies,
        "c_user": None,
    }
    await evt.reply("1. Log in to Facebook normally.\n"
                    "2. Press `F12` to open developer tools.\n"
                    "3. Select the \"Application\" (Chrome) or \"Storage\" (Firefox) tab.\n"
                    "4. In the sidebar, expand \"Cookies\" and select `https://www.facebook.com`.\n"
                    "5. In the cookie list, find the `c_user` row and double click on the value"
                    r", then copy the value and send it here.")


async def enter_login_cookies(evt: CommandEvent) -> None:
    if not evt.sender.command_status["c_user"]:
        if len(evt.args) == 0:
            await evt.reply("Please enter the value of the `c_user` cookie, or use "
                            "the `cancel` command to cancel.")
            return
        evt.sender.command_status["c_user"] = evt.args[0]
        await evt.reply("Now do the last step again, but find the value of the `xs` row instead.")
        return
    if len(evt.args) == 0:
        await evt.reply("Please enter the value of the `xs` cookie, or use "
                        "the `cancel` command to cancel.")
        return

    cookie = SimpleCookie()
    cookie["c_user"] = evt.sender.command_status["c_user"]
    cookie["xs"] = evt.args[0]
    ok = await evt.sender.setSession(cookie) and await evt.sender.is_logged_in(True)
    if not ok:
        await evt.reply("Failed to log in (see logs for more details)")
    else:
        await evt.sender.onLoggedIn(evt.sender.command_status["c_user"])
    evt.sender.command_status = None


@command_handler(needs_auth=True, help_section=SECTION_AUTH, help_text="Log out of Facebook")
async def logout(evt: CommandEvent) -> None:
    puppet = pu.Puppet.get_by_fbid(evt.sender.uid)
    await evt.sender.logout()
    if puppet.is_real_user:
        await puppet.switch_mxid(None, None)


@command_handler(needs_auth=True, management_only=True, help_args="<_access token_>",
                 help_section=SECTION_AUTH, help_text="Replace your Facebook Messenger account's "
                                                      "Matrix puppet with your Matrix account")
async def login_matrix(evt: CommandEvent) -> None:
    puppet = pu.Puppet.get_by_fbid(evt.sender.uid)
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
    puppet = pu.Puppet.get_by_fbid(evt.sender.uid)
    if not puppet.is_real_user:
        await evt.reply("You're not logged in with your Matrix account")
        return
    await puppet.switch_mxid(None, None)
    await evt.reply("Restored the original puppet for your Facebook Messenger account")
