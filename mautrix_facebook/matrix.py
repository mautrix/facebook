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
from typing import Tuple, TYPE_CHECKING
import logging
import asyncio

from mautrix.types import (EventID, RoomID, UserID, Event, EventType, MessageEvent, MessageType,
                           MessageEventContent, StateEvent, Membership, RedactionEvent,
                           PresenceEvent, TypingEvent, ReceiptEvent, PresenceState)
from mautrix.errors import IntentError, MatrixError

from . import user as u, portal as po, puppet as pu, commands as com

if TYPE_CHECKING:
    from .context import Context


class MatrixHandler:
    log: logging.Logger = logging.getLogger("mau.mx")
    commands: com.CommandProcessor

    def __init__(self, context: 'Context') -> None:
        self.az, self.config, _ = context.core
        self.commands = com.CommandProcessor(context)
        self.az.matrix_event_handler(self.handle_event)

    async def init_as_bot(self) -> None:
        displayname = self.config["appservice.bot_displayname"]
        if displayname:
            try:
                await self.az.intent.set_displayname(
                    displayname if displayname != "remove" else "")
            except asyncio.TimeoutError:
                self.log.exception("TimeoutError when trying to set displayname")

        avatar = self.config["appservice.bot_avatar"]
        if avatar:
            try:
                await self.az.intent.set_avatar_url(avatar if avatar != "remove" else "")
            except asyncio.TimeoutError:
                self.log.exception("TimeoutError when trying to set avatar")

    async def accept_bot_invite(self, room_id: RoomID, inviter: u.User) -> None:
        tries = 0
        while tries < 5:
            try:
                await self.az.intent.join_room(room_id)
                break
            except (IntentError, MatrixError):
                tries += 1
                wait_for_seconds = (tries + 1) * 10
                if tries < 5:
                    self.log.exception(f"Failed to join room {room_id} with bridge bot, "
                                       f"retrying in {wait_for_seconds} seconds...")
                    await asyncio.sleep(wait_for_seconds)
                else:
                    self.log.exception("Failed to join room {room}, giving up.")
                    return

        if not inviter.is_whitelisted:
            await self.az.intent.send_notice(
                room_id,
                text="You are not whitelisted to use this bridge.\n\n"
                     "If you are the owner of this bridge, see the bridge.permissions "
                     "section in your config file.",
                html="<p>You are not whitelisted to use this bridge.</p>"
                     "<p>If you are the owner of this bridge, see the "
                     "<code>bridge.permissions</code> section in your config file.</p>")
            await self.az.intent.leave_room(room_id)

    async def handle_invite(self, room_id: RoomID, user_id: UserID, inviter_mxid: UserID) -> None:
        self.log.debug(f"{inviter_mxid} invited {user_id} to {room_id}")
        inviter = u.User.get_by_mxid(inviter_mxid)
        if inviter is None:
            self.log.exception(f"Failed to find user with Matrix ID {inviter_mxid}")
        if user_id == self.az.bot_mxid:
            return await self.accept_bot_invite(room_id, inviter)
        elif not inviter.is_whitelisted:
            return

        # TODO handle puppet and user invites for group chats

        # The rest can probably be ignored

    async def handle_join(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = u.User.get_by_mxid(user_id)

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if not user.is_whitelisted:
            await portal.main_intent.kick_user(room_id, user.mxid,
                                               "You are not whitelisted on this "
                                               "Facebook Messenger bridge.")
            return
        elif not await user.is_logged_in():
            await portal.main_intent.kick_user(room_id, user.mxid, "You are not logged in to this "
                                                                   "Facebook Messenger bridge.")
            return

        self.log.debug(f"{user} joined {room_id}")
        # await portal.join_matrix(user, event_id)

    @staticmethod
    async def handle_redaction(room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_redaction(user, event_id)

    def is_command(self, message: MessageEventContent) -> Tuple[bool, str]:
        text = message.body
        prefix = self.config["bridge.command_prefix"]
        is_command = text.startswith(prefix)
        if is_command:
            text = text[len(prefix) + 1:]
        return is_command, text

    async def handle_message(self, room: RoomID, sender_id: UserID, message: MessageEventContent,
                             event_id: EventID) -> None:
        is_command, text = self.is_command(message)
        sender = u.User.get_by_mxid(sender_id)
        if not sender or not sender.is_whitelisted:
            self.log.debug(f"Ignoring message \"{message}\" from {sender} to {room}:"
                           " User is not whitelisted.")
            return
        self.log.debug(f"Received Matrix event \"{message}\" from {sender} in {room}")

        portal = po.Portal.get_by_mxid(room)
        if not is_command and portal and await sender.is_logged_in():
            await portal.handle_matrix_message(sender, message, event_id)
            return

        if message.msgtype != MessageType.TEXT:
            return

        try:
            is_management = len(await self.az.intent.get_room_members(room)) == 2
        except MatrixError:
            self.log.exception("hmm")
            # The AS bot is not in the room.
            return

        if is_command or is_management:
            try:
                command, arguments = text.split(" ", 1)
                args = arguments.split(" ")
            except ValueError:
                # Not enough values to unpack, i.e. no arguments
                command = text
                args = []
            await self.commands.handle(room, event_id, sender, command, args, is_management,
                                       is_portal=portal is not None)

    async def handle_presence(self, evt: PresenceEvent) -> None:
        if not self.config["bridge.presence"]:
            return
        user = u.User.get_by_mxid(evt.sender, create=False)
        user.setActiveStatus(evt.content.presence == PresenceState.ONLINE)

    async def handle_typing(self, evt: TypingEvent) -> None:
        pass

    @staticmethod
    async def handle_receipt(evt: ReceiptEvent) -> None:
        # These events come from custom puppet syncing, so there's always only one user.
        event_id, receipts = evt.content.popitem()
        receipt_type, users = receipts.popitem()
        user_id, data = users.popitem()

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return

        await user.markAsRead(portal.fbid)

    def filter_matrix_event(self, evt: Event) -> bool:
        if not isinstance(evt, (MessageEvent, StateEvent)):
            return False
        return (evt.sender == self.az.bot_mxid
                or pu.Puppet.get_id_from_mxid(evt.sender) is not None)

    async def try_handle_event(self, evt: Event) -> None:
        try:
            await self.handle_event(evt)
        except Exception:
            self.log.exception("Error handling manually received Matrix event")

    async def handle_event(self, evt: Event) -> None:
        if self.filter_matrix_event(evt):
            return
        self.log.debug("Received event: %s", evt)

        if evt.type == EventType.ROOM_MEMBER:
            evt: StateEvent
            if evt.content.membership == Membership.INVITE:
                await self.handle_invite(evt.room_id, UserID(evt.state_key), evt.sender)
        elif evt.type in (EventType.ROOM_MESSAGE, EventType.STICKER):
            evt: MessageEvent
            if evt.type != EventType.ROOM_MESSAGE:
                evt.content.msgtype = MessageType(str(evt.type))
            await self.handle_message(evt.room_id, evt.sender, evt.content, evt.event_id)
        elif evt.type == EventType.ROOM_REDACTION:
            evt: RedactionEvent
            await self.handle_redaction(evt.room_id, evt.sender, evt.redacts)
        elif evt.type == EventType.PRESENCE:
            await self.handle_presence(evt)
        elif evt.type == EventType.TYPING:
            await self.handle_typing(evt)
        elif evt.type == EventType.RECEIPT:
            await self.handle_receipt(evt)
