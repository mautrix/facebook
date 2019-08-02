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
from typing import List, TYPE_CHECKING

from fbchat.models import ThreadType
from mautrix.types import (EventID, RoomID, UserID, Event, EventType, MessageEvent, StateEvent,
                           RedactionEvent, PresenceEventContent, ReceiptEvent, PresenceState,
                           ReactionEvent, ReactionEventContent, RelationType)
from mautrix.errors import MatrixError
from mautrix.bridge import BaseMatrixHandler

from . import user as u, portal as po, puppet as pu, commands as c

if TYPE_CHECKING:
    from .context import Context


class MatrixHandler(BaseMatrixHandler):
    def __init__(self, context: 'Context') -> None:
        super().__init__(context.az, context.config, command_processor=c.CommandProcessor(context))

    async def get_portal(self, room_id: RoomID) -> 'po.Portal':
        return po.Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID) -> 'pu.Puppet':
        return pu.Puppet.get_by_mxid(user_id, create=False)

    async def get_user(self, user_id: UserID) -> 'u.User':
        return u.User.get_by_mxid(user_id)

    async def handle_puppet_invite(self, room_id: RoomID, puppet: 'pu.Puppet', invited_by: 'u.User'
                                   ) -> None:
        intent = puppet.default_mxid_intent
        self.log.debug(f"{invited_by.mxid} invited puppet for {puppet.fbid} to {room_id}")
        if not await invited_by.is_logged_in():
            await intent.error_and_leave(room_id, text="Please log in before inviting Facebook "
                                                       "Messenger puppets to private chats.")
            return

        portal = po.Portal.get_by_mxid(room_id)
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
            members = []
        if len(members) > 2:
            # TODO add facebook group creating
            await intent.send_notice(room_id, "You can not invite Facebook Messenger puppets to "
                                              "multi-user rooms.")
            await intent.leave_room(room_id)
            return
        portal = po.Portal.get_by_fbid(puppet.fbid, invited_by.uid, ThreadType.USER)
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
        portal.save()
        await intent.send_notice(room_id, "Portal to private chat created.")

    async def handle_invite(self, room_id: RoomID, user_id: UserID, invited_by: 'u.User') -> None:
        # TODO handle puppet and user invites for group chats
        # The rest can probably be ignored
        pass

    async def handle_join(self, room_id: RoomID, user_id: UserID) -> None:
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

    async def handle_leave(self, room_id: RoomID, user_id: UserID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return

        await portal.handle_matrix_leave(user)

    @staticmethod
    async def handle_redaction(room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_redaction(user, event_id)

    @classmethod
    async def handle_reaction(cls, room_id: RoomID, user_id: UserID, event_id: EventID,
                              content: ReactionEventContent) -> None:
        if content.relates_to.rel_type != RelationType.ANNOTATION:
            cls.log.debug(f"Ignoring m.reaction event in {room_id} from {user_id} with unexpected "
                          f"relation type {content.relates_to.rel_type}")
            return
        user = u.User.get_by_mxid(user_id)
        if not user:
            return

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_reaction(user, event_id, content.relates_to.event_id,
                                            content.relates_to.key)

    async def handle_presence(self, user_id: UserID, info: PresenceEventContent) -> None:
        if not self.config["bridge.presence"]:
            return
        user = u.User.get_by_mxid(user_id, create=False)
        if user:
            user.setActiveStatus(info.presence == PresenceState.ONLINE)

    @staticmethod
    async def handle_typing(room_id: RoomID, typing: List[UserID]) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        users = (u.User.get_by_mxid(mxid, create=False) for mxid in typing)
        await portal.handle_matrix_typing({user for user in users
                                           if user is not None})

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

    async def handle_event(self, evt: Event) -> None:
        if evt.type == EventType.ROOM_REDACTION:
            evt: RedactionEvent
            await self.handle_redaction(evt.room_id, evt.sender, evt.redacts)
        elif evt.type == EventType.REACTION:
            evt: ReactionEvent
            await self.handle_reaction(evt.room_id, evt.sender, evt.event_id, evt.content)
        elif evt.type == EventType.PRESENCE:
            await self.handle_presence(evt.sender, evt.content)
        elif evt.type == EventType.TYPING:
            await self.handle_typing(evt.room_id, evt.content.user_ids)
        elif evt.type == EventType.RECEIPT:
            await self.handle_receipt(evt)
