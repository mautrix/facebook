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

from typing import TYPE_CHECKING
import time

from mautrix.bridge import BaseMatrixHandler
from mautrix.errors import MatrixError
from mautrix.types import (
    Event,
    EventID,
    EventType,
    MessageType,
    PresenceEvent,
    PresenceEventContent,
    ReactionEvent,
    ReactionEventContent,
    ReceiptEvent,
    RedactionEvent,
    RelationType,
    RoomID,
    SingleReceiptEventContent,
    TextMessageEventContent,
    TypingEvent,
    UserID,
)

from . import portal as po, puppet as pu, user as u
from .db import Message as DBMessage, ThreadType

if TYPE_CHECKING:
    from .__main__ import MessengerBridge


class MatrixHandler(BaseMatrixHandler):
    def __init__(self, bridge: "MessengerBridge") -> None:
        prefix, suffix = bridge.config["bridge.username_template"].format(userid=":").split(":")
        homeserver = bridge.config["homeserver.domain"]
        self.user_id_prefix = f"@{prefix}"
        self.user_id_suffix = f"{suffix}:{homeserver}"
        super().__init__(bridge=bridge)

    async def send_welcome_message(self, room_id: RoomID, inviter: u.User) -> None:
        await super().send_welcome_message(room_id, inviter)
        if not inviter.notice_room:
            inviter.notice_room = room_id
            await inviter.save()
            await self.az.intent.send_notice(
                room_id, "This room has been marked as your Facebook Messenger bridge notice room."
            )

    async def handle_invite(
        self, room_id: RoomID, user_id: UserID, invited_by: u.User, event_id: EventID
    ) -> None:
        # TODO handle puppet and user invites for group chats
        # The rest can probably be ignored
        pass

    async def handle_join(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = await u.User.get_by_mxid(user_id)

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if not user.relay_whitelisted:
            await portal.main_intent.kick_user(
                room_id, user.mxid, "You are not whitelisted on this Facebook Messenger bridge."
            )
            return
        elif (
            not await user.is_logged_in()
            and not portal.has_relay
            and not self.config["bridge.allow_invites"]
        ):
            await portal.main_intent.kick_user(
                room_id, user.mxid, "You are not logged in to this Facebook Messenger bridge."
            )
            return

        self.log.debug(f"{user.mxid} joined {room_id}")
        # await portal.join_matrix(user, event_id)

    async def handle_leave(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        user = await u.User.get_by_mxid(user_id, create=False)
        if not user:
            return

        await portal.handle_matrix_leave(user)

    @staticmethod
    async def handle_redaction(
        room_id: RoomID, user_id: UserID, event_id: EventID, redaction_event_id: EventID
    ) -> None:
        user = await u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_redaction(user, event_id, redaction_event_id)

    @classmethod
    async def handle_reaction(
        cls,
        room_id: RoomID,
        user_id: UserID,
        event_id: EventID,
        content: ReactionEventContent,
    ) -> None:
        if content.relates_to.rel_type != RelationType.ANNOTATION:
            cls.log.debug(
                f"Ignoring m.reaction event in {room_id} from {user_id} with unexpected "
                f"relation type {content.relates_to.rel_type}"
            )
            return
        user = await u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_reaction(
            user, event_id, content.relates_to.event_id, content.relates_to.key
        )

    @staticmethod
    async def handle_typing(room_id: RoomID, typing: list[UserID]) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.is_direct:
            return

        await portal.handle_matrix_typing(set(typing))

    async def handle_read_receipt(
        self,
        user: u.User,
        portal: po.Portal,
        event_id: EventID,
        data: SingleReceiptEventContent,
    ) -> None:
        if not user.mqtt:
            return
        timestamp = data.get("ts", int(time.time() * 1000))
        message = await DBMessage.get_by_mxid(event_id, portal.mxid)
        await user.mqtt.mark_read(
            portal.fbid,
            portal.fb_type != ThreadType.USER,
            read_to=message.timestamp if message else timestamp,
        )

    async def handle_ephemeral_event(
        self, evt: ReceiptEvent | PresenceEvent | TypingEvent
    ) -> None:
        if evt.type == EventType.TYPING:
            await self.handle_typing(evt.room_id, evt.content.user_ids)
        elif evt.type == EventType.RECEIPT:
            await self.handle_receipt(evt)

    async def handle_event(self, evt: Event) -> None:
        if evt.type == EventType.ROOM_REDACTION:
            evt: RedactionEvent
            await self.handle_redaction(evt.room_id, evt.sender, evt.redacts, evt.event_id)
        elif evt.type == EventType.REACTION:
            evt: ReactionEvent
            await self.handle_reaction(evt.room_id, evt.sender, evt.event_id, evt.content)
