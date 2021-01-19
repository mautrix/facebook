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

from mautrix.client import Client
from mautrix.bridge.commands import HelpSection, command_handler
from mautrix.bridge import custom_puppet as cpu

from maufbapi import AndroidState, AndroidAPI
from maufbapi.http import TwoFactorRequired, OAuthException, IncorrectPassword

from .. import puppet as pu
from .typehint import CommandEvent

SECTION_AUTH = HelpSection("Authentication", 10, "")


async def check_approved_login(state: AndroidState, api: AndroidAPI, evt: CommandEvent) -> None:
    while evt.sender.command_status and evt.sender.command_status["action"] == "Login":
        await asyncio.sleep(5)
        try:
            was_approved = await api.check_approved_machine()
        except Exception as e:
            evt.log.exception("Error checking if login was approved from another device")
            await evt.reply(f"Error checking if login was approved from another device: {e}")
            break
        if was_approved:
            prev_cmd_status = evt.sender.command_status
            evt.sender.command_status = None
            try:
                await api.login_approved()
            except TwoFactorRequired:
                await evt.reply("Login approved from another device, but Facebook decided that "
                                "you need to enter the 2FA code anyway.")
                evt.sender.command_status = prev_cmd_status
                return
            await evt.sender.on_logged_in(state)
            await evt.reply("Login successfully approved from another device")
            break


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
    state = AndroidState()
    state.generate(evt.sender.mxid)
    api = AndroidAPI(state, log=evt.sender.log.getChild("login-api"))
    try:
        await api.mobile_config_sessionless()
        await api.login(evt.args[0], " ".join(evt.args[1:]))
        await evt.sender.on_logged_in(state)
        await evt.reply("Successfully logged in")
    except TwoFactorRequired:
        await evt.reply("You have two-factor authentication turned on. Please either send the code"
                        " from SMS or your authenticator app here, or approve the login from"
                        " another device logged into Messenger.")
        checker_task = asyncio.create_task(check_approved_login(state, api, evt))
        evt.sender.command_status = {
            "action": "Login",
            "room_id": evt.room_id,
            "next": enter_2fa_code,
            "state": state,
            "api": api,
            "email": evt.args[0],
            "checker_task": checker_task,
        }
    except OAuthException as e:
        await evt.reply(f"Error from Messenger:\n\n> {e}")
    except Exception as e:
        evt.sender.command_status = None
        await evt.reply(f"Failed to log in: {e}")
        evt.log.exception("Failed to log in")


async def enter_2fa_code(evt: CommandEvent) -> None:
    checker_task: asyncio.Task = evt.sender.command_status["checker_task"]
    checker_task.cancel()
    state: AndroidState = evt.sender.command_status["state"]
    api: AndroidAPI = evt.sender.command_status["api"]
    email: str = evt.sender.command_status["email"]
    try:
        await api.login_2fa(email, "".join(evt.args).strip())
        await evt.sender.on_logged_in(state)
        await evt.reply("Successfully logged in")
        evt.sender.command_status = None
    except IncorrectPassword:
        await evt.reply("Incorrect two-factor authentication code. Pleaase try again.")
    except OAuthException as e:
        await evt.reply(f"Error from Messenger:\n\n> {e}")
        evt.sender.command_status = None
    except Exception as e:
        evt.sender.command_status = None
        await evt.reply(f"Failed to log in: {e}")
        evt.log.exception("Failed to log in")


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
