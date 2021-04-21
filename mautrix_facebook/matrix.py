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
from typing import List, Union, TYPE_CHECKING
import time

from mautrix.types import (EventID, RoomID, UserID, Event, EventType, MessageEvent, StateEvent,
                           RedactionEvent, PresenceEventContent, ReceiptEvent, PresenceState,
                           ReactionEvent, ReactionEventContent, RelationType, PresenceEvent,
                           TypingEvent, TextMessageEventContent, MessageType, EncryptedEvent,
                           SingleReceiptEventContent)
from mautrix.errors import MatrixError
from mautrix.bridge import BaseMatrixHandler

from . import user as u, portal as po, puppet as pu
from .db import ThreadType, Message as DBMessage

if TYPE_CHECKING:
    from .__main__ import MessengerBridge


class MatrixHandler(BaseMatrixHandler):
    def __init__(self, bridge: 'MessengerBridge') -> None:
        prefix, suffix = bridge.config["bridge.username_template"].format(userid=":").split(":")
        homeserver = bridge.config["homeserver.domain"]
        self.user_id_prefix = f"@{prefix}"
        self.user_id_suffix = f"{suffix}:{homeserver}"
        super().__init__(bridge=bridge)

    async def send_welcome_message(self, room_id: RoomID, inviter: 'u.User') -> None:
        await super().send_welcome_message(room_id, inviter)
        if not inviter.notice_room:
            inviter.notice_room = room_id
            await inviter.save()
            await self.az.intent.send_notice(room_id, "This room has been marked as your "
                                                      "Facebook Messenger bridge notice room.")

    async def handle_puppet_invite(self, room_id: RoomID, puppet: 'pu.Puppet',
                                   invited_by: 'u.User', event_id: EventID) -> None:
        intent = puppet.default_mxid_intent
        self.log.debug(f"{invited_by.mxid} invited puppet for {puppet.fbid} to {room_id}")
        if not await invited_by.is_logged_in():
            await intent.error_and_leave(room_id, text="Please log in before inviting Facebook "
                                                       "Messenger puppets to private chats.")
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if portal:
            if portal.is_direct:
                await intent.error_and_leave(room_id, text="You can not invite additional users "
                                                           "to private chats.")
                return
            # TODO add facebook inviting
            # await portal.invite_facebook(inviter, puppet)
            # await intent.join_room(room_id)
            return
        await intent.join_room(room_id)
        try:
            members = await intent.get_room_members(room_id)
        except MatrixError:
            self.log.exception(f"Failed to get member list after joining {room_id}")
            await intent.leave_room(room_id)
            return
        if len(members) > 2:
            # TODO add facebook group creating
            await intent.send_notice(room_id, "You can not invite Facebook Messenger puppets to "
                                              "multi-user rooms.")
            await intent.leave_room(room_id)
            return
        portal = await po.Portal.get_by_fbid(puppet.fbid, fb_receiver=invited_by.fbid,
                                             fb_type=ThreadType.USER)
        if portal.mxid:
            try:
                await intent.invite_user(portal.mxid, invited_by.mxid, check_cache=False)
                await intent.send_notice(room_id,
                                         text=("You already have a private chat with me "
                                               f"in room {portal.mxid}"),
                                         html=("You already have a private chat with me: "
                                               f"<a href='https://matrix.to/#/{portal.mxid}'>"
                                               "Link to room"
                                               "</a>"))
                await intent.leave_room(room_id)
                return
            except MatrixError:
                pass
        portal.mxid = room_id
        e2be_ok = await portal.check_dm_encryption()
        await portal.save()
        if e2be_ok is True:
            evt_type, content = await self.e2ee.encrypt(
                room_id, EventType.ROOM_MESSAGE,
                TextMessageEventContent(msgtype=MessageType.NOTICE,
                                        body="Portal to private chat created and end-to-bridge"
                                             " encryption enabled."))
            await intent.send_message_event(room_id, evt_type, content)
        else:
            message = "Portal to private chat created."
            if e2be_ok is False:
                message += "\n\nWarning: Failed to enable end-to-bridge encryption"
            await intent.send_notice(room_id, message)

    async def handle_invite(self, room_id: RoomID, user_id: UserID, invited_by: 'u.User',
                            event_id: EventID) -> None:
        # TODO handle puppet and user invites for group chats
        # The rest can probably be ignored
        pass

    async def handle_join(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = await u.User.get_by_mxid(user_id)

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if not user.is_whitelisted:
            await portal.main_intent.kick_user(room_id, user.mxid,
                                               "You are not whitelisted on this "
                                               "Facebook Messenger bridge.")
            return
        elif not await user.is_logged_in() and not self.config["bridge.allow_invites"]:
            await portal.main_intent.kick_user(room_id, user.mxid, "You are not logged in to this "
                                                                   "Facebook Messenger bridge.")
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
    async def handle_redaction(room_id: RoomID, user_id: UserID, event_id: EventID,
                               redaction_event_id: EventID) -> None:
        user = await u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_redaction(user, event_id, redaction_event_id)

    @classmethod
    async def handle_reaction(cls, room_id: RoomID, user_id: UserID, event_id: EventID,
                              content: ReactionEventContent) -> None:
        if content.relates_to.rel_type != RelationType.ANNOTATION:
            cls.log.debug(f"Ignoring m.reaction event in {room_id} from {user_id} with unexpected "
                          f"relation type {content.relates_to.rel_type}")
            return
        user = await u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_reaction(user, event_id, content.relates_to.event_id,
                                            content.relates_to.key)

    async def handle_presence(self, user_id: UserID, info: PresenceEventContent) -> None:
        if not self.config["bridge.presence"]:
            return
        # user = await u.User.get_by_mxid(user_id, create=False)
        # if user and user.mqtt:
        #     user.log.debug(f"Setting foreground status to {info.presence == PresenceState.ONLINE}")
        #     user.mqtt.set_foreground(info.presence == PresenceState.ONLINE)

    @staticmethod
    async def handle_typing(room_id: RoomID, typing: List[UserID]) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        # FIXME
        # users = [await u.User.get_by_mxid(mxid, create=False) for mxid in typing]
        # await portal.handle_matrix_typing({user for user in users
        #                                    if user is not None})

    async def handle_read_receipt(self, user: 'u.User', portal: 'po.Portal', event_id: EventID,
                                  data: SingleReceiptEventContent) -> None:
        if not user.mqtt:
            return
        timestamp = data.get("ts", int(time.time() * 1000))
        message = await DBMessage.get_by_mxid(event_id, portal.mxid)
        await user.mqtt.mark_read(portal.fbid, portal.fb_type != ThreadType.USER,
                                  read_to=message.timestamp if message else timestamp)

    def filter_matrix_event(self, evt: Event) -> bool:
        if isinstance(evt, (ReceiptEvent, TypingEvent, PresenceEvent)):
            return False
        elif not isinstance(evt, (ReactionEvent, RedactionEvent, MessageEvent, StateEvent,
                                  EncryptedEvent)):
            return True
        return (evt.sender == self.az.bot_mxid
                or pu.Puppet.get_id_from_mxid(evt.sender) is not None)

    async def handle_ephemeral_event(self, evt: Union[ReceiptEvent, PresenceEvent, TypingEvent]
                                     ) -> None:
        if evt.type == EventType.PRESENCE:
            await self.handle_presence(evt.sender, evt.content)
        elif evt.type == EventType.TYPING:
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
