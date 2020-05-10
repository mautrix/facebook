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
from typing import Any, Dict, Iterator, Optional, Iterable, Awaitable, TYPE_CHECKING
from http.cookies import SimpleCookie
import asyncio
import logging

import fbchat
from mautrix.types import UserID, PresenceState
from mautrix.appservice import AppService
from mautrix.client import Client as MxClient
from mautrix.bridge._community import CommunityHelper, CommunityID

from .config import Config
from .commands import enter_2fa_code
from .db import User as DBUser, UserPortal as DBUserPortal, Contact as DBContact, ThreadType
from . import portal as po, puppet as pu

if TYPE_CHECKING:
    from .context import Context

config: Config


class User:
    az: AppService
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.user")
    by_mxid: Dict[UserID, 'User'] = {}
    by_fbid: Dict[str, 'User'] = {}

    session: Optional[fbchat.Session]
    client: Optional[fbchat.Client]
    listener: Optional[fbchat.Listener]
    listen_task: Optional[asyncio.Task]
    user_agent: str

    command_status: Optional[Dict[str, Any]]
    is_whitelisted: bool
    is_admin: bool
    permission_level: str
    _is_logged_in: Optional[bool]
    _on_logged_in_done: bool
    _session_data: Optional[SimpleCookie]
    _db_instance: Optional[DBUser]

    _community_helper: CommunityHelper
    _community_id: Optional[CommunityID]

    def __init__(self, mxid: UserID, session: Optional[SimpleCookie] = None,
                 user_agent: Optional[str] = None, db_instance: Optional[DBUser] = None) -> None:
        self.mxid = mxid
        self.by_mxid[mxid] = self
        self.user_agent = user_agent
        self.command_status = None
        self.is_whitelisted, self.is_admin, self.permission_level = config.get_permissions(mxid)
        self._is_logged_in = None
        self._on_logged_in_done = False
        self._session_data = session
        self._db_instance = db_instance
        self._community_id = None

        self.log = self.log.getChild(self.mxid)

        self.client = None
        self.session = None
        self.listener = None
        self.listen_task = None

    # region Sessions

    @property
    def fbid(self) -> Optional[str]:
        if not self.session:
            return None
        return self.session.user.id

    @property
    def db_instance(self) -> DBUser:
        if not self._db_instance:
            self._db_instance = DBUser(mxid=self.mxid, session=self._session_data,
                                       fbid=self.fbid, user_agent=self.user_agent)
        return self._db_instance

    def save(self, _update_session_data: bool = True) -> None:
        self.log.debug("Saving session")
        if _update_session_data and self.session:
            self._session_data = self.session.get_cookies()
        self.db_instance.edit(session=self._session_data, fbid=self.fbid,
                              user_agent=self.user_agent)

    @classmethod
    def from_db(cls, db_user: DBUser) -> 'User':
        return User(mxid=db_user.mxid, session=db_user.session, user_agent=db_user.user_agent,
                    db_instance=db_user)

    @classmethod
    def get_all(cls) -> Iterator['User']:
        for db_user in DBUser.all():
            yield cls.from_db(db_user)

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True) -> Optional['User']:
        if pu.Puppet.get_id_from_mxid(mxid) is not None or mxid == cls.az.bot_mxid:
            return None
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        db_user = DBUser.get_by_mxid(mxid)
        if db_user:
            return cls.from_db(db_user)

        if create:
            user = cls(mxid)
            user.db_instance.insert()
            return user

        return None

    @classmethod
    def get_by_fbid(cls, fbid: str) -> Optional['User']:
        try:
            return cls.by_fbid[fbid]
        except KeyError:
            pass

        db_user = DBUser.get_by_fbid(fbid)
        if db_user:
            return cls.from_db(db_user)

        return None

    async def load_session(self) -> bool:
        if self._is_logged_in:
            return True
        elif not self._session_data:
            return False
        session = await fbchat.Session.from_cookies(self._session_data)
        if await session.is_logged_in():
            self.log.info("Loaded session successfully")
            self.session = session
            self.client = fbchat.Client(session=self.session)
            if self.listen_task:
                self.listen_task.cancel()
            self.listen_task = self.loop.create_task(self.try_listen())
            asyncio.ensure_future(self.post_login(), loop=self.loop)
            return True
        return False

    async def is_logged_in(self, _override: bool = False) -> bool:
        if not self.session:
            return False
        if self._is_logged_in is None or _override:
            self._is_logged_in = await self.session.is_logged_in()
        return self._is_logged_in

    # endregion

    async def logout(self) -> bool:
        ok = True
        if self.session:
            try:
                await self.session.logout()
            except fbchat.FacebookError:
                self.log.exception("Error while logging out")
                ok = False
        self._session_data = None
        self._is_logged_in = False
        self._on_logged_in_done = False
        self.client = None
        self.session = None
        self.save(_update_session_data=False)
        return ok

    async def post_login(self) -> None:
        self.log.info("Running post-login actions")
        self.by_fbid[self.fbid] = self

        try:
            puppet = pu.Puppet.get_by_fbid(self.fbid)

            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info(f"Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        await self._create_community()
        await self.sync_contacts()
        await self.sync_threads()
        self.log.debug("Updating own puppet info")
        # TODO this might not be right (if it is, check that we got something sensible?)
        own_info = await self.client.fetch_thread_info([self.fbid]).__anext__()
        puppet = pu.Puppet.get_by_fbid(self.fbid, create=True)
        await puppet.update_info(source=self, info=own_info)

    async def _create_community(self) -> None:
        template = config["bridge.community_template"]
        if not template:
            return
        localpart, server = MxClient.parse_user_id(self.mxid)
        community_localpart = template.format(localpart=localpart, server=server)
        self.log.debug(f"Creating personal filtering community {community_localpart}...")
        self._community_id, created = await self._community_helper.create(community_localpart)
        if created:
            await self._community_helper.update(self._community_id, name="Facebook Messenger",
                                                avatar_url=config["appservice.bot_avatar"],
                                                short_desc="Your Facebook bridged chats")
            await self._community_helper.invite(self._community_id, self.mxid)

    async def _add_community(self, up: Optional[DBUserPortal], contact: Optional[DBContact],
                             portal: 'po.Portal', puppet: Optional['pu.Puppet']) -> None:
        if portal.mxid:
            if not up or not up.in_community:
                ic = await self._community_helper.add_room(self._community_id, portal.mxid)
                if up and ic:
                    up.edit(in_community=True)
                elif not up:
                    DBUserPortal(user=self.fbid, in_community=ic, portal=portal.fbid,
                                 portal_receiver=portal.fb_receiver).insert()
        if puppet:
            await self._add_community_puppet(contact, puppet)

    async def _add_community_puppet(self, contact: Optional[DBContact],
                                    puppet: 'pu.Puppet') -> None:
        if not contact or not contact.in_community:
            await puppet.default_mxid_intent.ensure_registered()
            ic = await self._community_helper.join(self._community_id,
                                                   puppet.default_mxid_intent)
            if contact and ic:
                contact.edit(in_community=True)
            elif not contact:
                DBContact(user=self.fbid, contact=puppet.fbid, in_community=ic).insert()

    async def sync_contacts(self):
        try:
            self.log.debug("Fetching contacts...")
            users = await self.client.fetch_users()
            self.log.debug(f"Fetched {len(users)} contacts")
            contacts = DBContact.all(self.fbid)
            update_avatars = config["bridge.update_avatar_initial_sync"]
            for user in users:
                puppet = pu.Puppet.get_by_fbid(user.id, create=True)
                await puppet.update_info(self, user, update_avatar=update_avatars)
                await self._add_community_puppet(contacts.get(puppet.fbid, None), puppet)
        except Exception:
            self.log.exception("Failed to sync contacts")

    async def sync_threads(self) -> None:
        try:
            sync_count = min(20, config["bridge.initial_chat_sync"])
            if sync_count <= 0:
                return
            self.log.debug("Fetching threads...")
            ups = DBUserPortal.all(self.fbid)
            contacts = DBContact.all(self.fbid)
            async for thread in self.client.fetch_threads(limit=sync_count):
                if not isinstance(thread, (fbchat.UserData, fbchat.PageData, fbchat.GroupData)):
                    # TODO log?
                    continue
                self.log.debug(f"Syncing thread {thread.id} {thread.name}")
                fb_receiver = self.fbid if isinstance(thread, fbchat.User) else None
                portal = po.Portal.get_by_thread(thread, fb_receiver)
                puppet = None

                if isinstance(thread, fbchat.UserData):
                    puppet = pu.Puppet.get_by_fbid(thread.id, create=True)
                    await puppet.update_info(self, thread)

                await self._add_community(ups.get(portal.fbid, None),
                                          contacts.get(puppet.fbid, None) if puppet else None,
                                          portal, puppet)

                await portal.create_matrix_room(self, thread)
        except Exception:
            self.log.exception("Failed to sync threads")

    async def on_2fa_callback(self) -> str:
        if self.command_status and self.command_status.get("action", "") == "Login":
            future = self.loop.create_future()
            self.command_status["future"] = future
            self.command_status["next"] = enter_2fa_code
            await self.az.intent.send_notice(self.command_status["room_id"],
                                             "You have two-factor authentication enabled. "
                                             "Please send the code here.")
            return await future
        self.log.warning("Unexpected on2FACode call")
        # raise RuntimeError("No ongoing login command")

    # region Facebook event handling

    async def try_listen(self) -> None:
        try:
            await self.listen()
        except Exception:
            self.log.exception("Fatal error in listener")

    async def listen(self) -> None:
        self.listener = fbchat.Listener(session=self.session, chat_on=False, foreground=False)
        handlers = {
            fbchat.MessageEvent: self.on_message,
            fbchat.TitleSet: self.on_title_change,
        }
        self.log.debug("Starting fbchat listener")
        async for event in self.listener.listen():
            self.log.debug("Handling fbchat event %s", event)
            try:
                handler = handlers[type(event)]
            except KeyError:
                self.log.debug(f"Received unknown event type {type(event)}")
            else:
                await handler(event)

    def stop_listening(self) -> None:
        if self.listener:
            self.listener.disconnect()
        if self.listen_task:
            self.listen_task.cancel()

    async def on_logged_in(self, email: str = None) -> None:
        """
        Called when the client is successfully logged in

        :param email: The email of the client
        """
        if self._on_logged_in_done:
            self.log.warning("Got duplicate on_logged_in call, ignoring")
            return
        self._on_logged_in_done = True
        if self.command_status and self.command_status.get("action", "") == "Login":
            await self.az.intent.send_notice(self.command_status["room_id"],
                                             f"Successfully logged in with {email}")
        self.save()
        if self.listen_task:
            self.listen_task.cancel()
        self.listen_task = self.loop.create_task(self.try_listen())
        asyncio.ensure_future(self.post_login(), loop=self.loop)

    async def on_message(self, evt: fbchat.MessageEvent) -> None:
        self.log.debug(f"onMessage({evt})")

        fb_receiver = self.fbid if isinstance(evt.thread, fbchat.User) else None
        portal = po.Portal.get_by_thread(evt.thread, fb_receiver)
        puppet = pu.Puppet.get_by_fbid(evt.author.id)
        if not puppet.name:
            await puppet.update_info(self)
        await portal.handle_facebook_message(self, puppet, evt.message)

    async def on_title_change(self, evt: fbchat.TitleSet) -> None:
        portal = po.Portal.get_by_thread(evt.thread)
        if not portal:
            return
        sender = pu.Puppet.get_by_fbid(evt.author.id)
        if not sender:
            return
        # TODO find messageId for the event
        await portal.handle_facebook_name(self, sender, evt.title, str(evt.at.timestamp()))

    async def on_image_change(self, mid: str = None, author_id: str = None, new_image: str = None,
                              thread_id: str = None, thread_type: ThreadType = ThreadType.GROUP,
                              at: int = None, msg: Any = None) -> None:
        """
        Called when the client is listening, and somebody changes the image of a thread

        :param mid: The action ID
        :param author_id: The ID of the person who changed the image
        :param new_image: The ID of the new image
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        fb_receiver = self.fbid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver)
        if not portal:
            return
        sender = pu.Puppet.get_by_fbid(author_id)
        if not sender:
            return
        await portal.handle_facebook_photo(self, sender, new_image, mid)

    async def on_nickname_change(self, mid=None, author_id=None, changed_for=None,
                                 new_nickname=None,
                                 thread_id=None, thread_type=ThreadType.USER, at=None,
                                 metadata=None,
                                 msg=None) -> None:
        """
        Called when the client is listening, and somebody changes the nickname of a person

        :param mid: The action ID
        :param author_id: The ID of the person who changed the nickname
        :param changed_for: The ID of the person whom got their nickname changed
        :param new_nickname: The new nickname
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Nickname change from {} in {} ({}) for {}: {}".format(
                author_id, thread_id, thread_type.name, changed_for, new_nickname
            )
        )

    async def on_admin_added(self, mid=None, added_id=None, author_id=None, thread_id=None,
                             thread_type=ThreadType.GROUP, at=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody adds an admin to a group thread

        :param mid: The action ID
        :param added_id: The ID of the admin who got added
        :param author_id: The ID of the person who added the admins
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} added admin: {} in {}".format(author_id, added_id, thread_id))

    async def on_admin_removed(self, mid=None, removed_id=None, author_id=None, thread_id=None,
                               thread_type=ThreadType.GROUP, at=None, msg=None):
        """
        Called when the client is listening, and somebody removes an admin from a group thread

        :param mid: The action ID
        :param removed_id: The ID of the admin who got removed
        :param author_id: The ID of the person who removed the admins
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} removed admin: {} in {}".format(author_id, removed_id, thread_id))

    async def on_approval_mode_change(self, mid=None, approval_mode=None, author_id=None,
                                      thread_id=None, thread_type=ThreadType.GROUP, at=None,
                                      msg=None) -> None:
        """
        Called when the client is listening, and somebody changes approval mode in a group thread

        :param mid: The action ID
        :param approval_mode: True if approval mode is activated
        :param author_id: The ID of the person who changed approval mode
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        if approval_mode:
            self.log.info("{} activated approval mode in {}".format(author_id, thread_id))
        else:
            self.log.info("{} disabled approval mode in {}".format(author_id, thread_id))

    async def on_message_seen(self, seen_by: str = None, thread_id: str = None,
                              thread_type=ThreadType.USER, seen_at: int = None, at: int = None,
                              metadata: Any = None, msg: Any = None) -> None:
        """
        Called when the client is listening, and somebody marks a message as seen

        :param seen_by: The ID of the person who marked the message as seen
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param seen_at: A timestamp of when the person saw the message
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        fb_receiver = self.uid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver, thread_type)
        puppet = pu.Puppet.get_by_fbid(seen_by)
        await portal.handle_facebook_seen(self, puppet)

    async def on_message_delivered(self, msg_ids=None, delivered_for=None, thread_id=None,
                                   thread_type=ThreadType.USER, at=None, metadata=None, msg=None
                                   ) -> None:
        """
        Called when the client is listening, and somebody marks messages as delivered

        :param msg_ids: The messages that are marked as delivered
        :param delivered_for: The person that marked the messages as delivered
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Messages {} delivered to {} in {} ({}) at {}s".format(
                msg_ids, delivered_for, thread_id, thread_type.name, at
            )
        )

    async def on_marked_seen(self, threads=None, seen_at=None, at=None, metadata=None, msg=None
                             ) -> None:
        """
        Called when the client is listening, and the client has successfully marked threads as seen

        :param threads: The threads that were marked
        :param seen_at: A timestamp of when the threads were seen
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        """
        self.log.info(
            "Marked messages as seen in threads {} at {}s".format(
                [(x[0], x[1].name) for x in threads], seen_at
            )
        )

    async def on_message_unsent(self, mid: str = None, author_id: str = None,
                                thread_id: str = None, thread_type: ThreadType = None,
                                at: int = None, msg: Any = None) -> None:
        """
        Called when the client is listening, and someone unsends (deletes for everyone) a message

        :param mid: ID of the unsent message
        :param author_id: The ID of the person who unsent the message
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        fb_receiver = self.uid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver, thread_type)
        puppet = pu.Puppet.get_by_fbid(author_id)
        await portal.handle_facebook_unsend(self, puppet, mid)

    async def on_people_added(self, mid=None, added_ids=None, author_id=None, thread_id=None,
                              at=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody adds people to a group thread

        :param mid: The action ID
        :param added_ids: The IDs of the people who got added
        :param author_id: The ID of the person who added the people
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info(
            "{} added: {} in {}".format(author_id, ", ".join(added_ids), thread_id)
        )

    async def on_person_removed(self, mid=None, removed_id=None, author_id=None, thread_id=None,
                                at=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody removes a person from a group thread

        :param mid: The action ID
        :param removed_id: The ID of the person who got removed
        :param author_id: The ID of the person who removed the person
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} removed: {} in {}".format(author_id, removed_id, thread_id))

    async def on_friend_request(self, from_id=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody sends a friend request

        :param from_id: The ID of the person that sent the request
        :param msg: A full set of the data recieved
        """
        self.log.info("Friend request from {}".format(from_id))

    async def on_inbox(self, unseen=None, unread=None, recent_unread=None, msg=None) -> None:
        """
        .. todo::
            Documenting this

        :param unseen: --
        :param unread: --
        :param recent_unread: --
        :param msg: A full set of the data recieved
        """
        self.log.info("Inbox event: {}, {}, {}".format(unseen, unread, recent_unread))

    async def on_typing(self, author_id=None, status=None, thread_id=None, thread_type=None,
                        msg=None) -> None:
        """
        Called when the client is listening, and somebody starts or stops typing into a chat

        :param author_id: The ID of the person who sent the action
        :param status: The typing status
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(f"User is typing: {author_id} {status} in {thread_id} {thread_type}")

    async def on_game_played(self, mid=None, author_id=None, game_id=None, game_name=None,
                             score=None, leaderboard=None, thread_id=None, thread_type=None,
                             at=None,
                             metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody plays a game

        :param mid: The action ID
        :param author_id: The ID of the person who played the game
        :param game_id: The ID of the game
        :param game_name: Name of the game
        :param score: Score obtained in the game
        :param leaderboard: Actual leaderboard of the game in the thread
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            '{} played "{}" in {} ({})'.format(
                author_id, game_name, thread_id, thread_type.name
            )
        )

    async def on_reaction_added(self, mid: str = None, reaction = None,
                                author_id: str = None, thread_id: str = None,
                                thread_type: ThreadType = None, at: int = None, msg: Any = None
                                ) -> None:
        """
        Called when the client is listening, and somebody reacts to a message

        :param mid: Message ID, that user reacted to
        :param reaction: Reaction
        :param author_id: The ID of the person who reacted to the message
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type reaction: models.MessageReaction
        :type thread_type: models.ThreadType
        """
        self.log.debug(f"onReactionAdded({mid}, {reaction}, {author_id}, {thread_id}, "
                       f"{thread_type})")
        fb_receiver = self.uid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver, thread_type)
        puppet = pu.Puppet.get_by_fbid(author_id)
        await portal.handle_facebook_reaction_add(self, puppet, mid, reaction.value)

    async def on_reaction_removed(self, mid: str = None, author_id: str = None,
                                  thread_id: str = None, thread_type: ThreadType = None,
                                  at: int = None, msg: Any = None) -> None:
        """
        Called when the client is listening, and somebody removes reaction from a message

        :param mid: Message ID, that user reacted to
        :param author_id: The ID of the person who removed reaction
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.debug(f"onReactionRemoved({mid}, {author_id}, {thread_id}, {thread_type})")
        fb_receiver = self.uid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver, thread_type)
        puppet = pu.Puppet.get_by_fbid(author_id)
        await portal.handle_facebook_reaction_remove(self, puppet, mid)

    async def on_block(self, author_id=None, thread_id=None, thread_type=None, at=None, msg=None
                       ) -> None:
        """
        Called when the client is listening, and somebody blocks client

        :param author_id: The ID of the person who blocked
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} blocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    async def on_unblock(self, author_id=None, thread_id=None, thread_type=None, at=None, msg=None
                         ) -> None:
        """
        Called when the client is listening, and somebody blocks client

        :param author_id: The ID of the person who unblocked
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} unblocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    async def on_live_location(self, mid=None, location=None, author_id=None, thread_id=None,
                               thread_type=None, at=None, msg=None) -> None:
        """
        Called when the client is listening and somebody sends live location info

        :param mid: The action ID
        :param location: Sent location info
        :param author_id: The ID of the person who sent location info
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        :type location: models.LiveLocationAttachment
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} sent live location info in {} ({}) with latitude {} and longitude {}".format(
                author_id, thread_id, thread_type, location.latitude, location.longitude
            )
        )

    async def on_call_started(self, mid=None, caller_id=None, is_video_call=None, thread_id=None,
                              thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        .. todo::
            Make this work with private calls

        Called when the client is listening, and somebody starts a call in a group

        :param mid: The action ID
        :param caller_id: The ID of the person who started the call
        :param is_video_call: True if it's video call
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} started call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    async def on_call_ended(self, mid=None, caller_id=None, is_video_call=None, call_duration=None,
                            thread_id=None, thread_type=None, at=None, metadata=None, msg=None
                            ) -> None:
        """
        .. todo::
            Make this work with private calls

        Called when the client is listening, and somebody ends a call in a group

        :param mid: The action ID
        :param caller_id: The ID of the person who ended the call
        :param is_video_call: True if it was video call
        :param call_duration: Call duration in seconds
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} ended call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    async def on_user_joined_call(self, mid=None, joined_id=None, is_video_call=None,
                                  thread_id=None,
                                  thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody joins a group call

        :param mid: The action ID
        :param joined_id: The ID of the person who joined the call
        :param is_video_call: True if it's video call
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} joined call in {} ({})".format(joined_id, thread_id, thread_type.name)
        )

    async def on_poll_created(self, mid=None, poll=None, author_id=None, thread_id=None,
                              thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody creates a group poll

        :param mid: The action ID
        :param poll: Created poll
        :param author_id: The ID of the person who created the poll
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type poll: models.Poll
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} created poll {} in {} ({})".format(
                author_id, poll, thread_id, thread_type.name
            )
        )

    async def on_poll_voted(self, mid=None, poll=None, added_options=None, removed_options=None,
                            author_id=None, thread_id=None, thread_type=None, at=None,
                            metadata=None,
                            msg=None) -> None:
        """
        Called when the client is listening, and somebody votes in a group poll

        :param mid: The action ID
        :param poll: Poll, that user voted in
        :param author_id: The ID of the person who voted in the poll
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type poll: models.Poll
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} voted in poll {} in {} ({})".format(
                author_id, poll, thread_id, thread_type.name
            )
        )

    async def on_plan_created(self, mid=None, plan=None, author_id=None, thread_id=None,
                              thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody creates a plan

        :param mid: The action ID
        :param plan: Created plan
        :param author_id: The ID of the person who created the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} created plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    async def on_plan_ended(self, mid=None, plan=None, thread_id=None, thread_type=None, at=None,
                            metadata=None, msg=None):
        """
        Called when the client is listening, and a plan ends

        :param mid: The action ID
        :param plan: Ended plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Plan {} has ended in {} ({})".format(plan, thread_id, thread_type.name)
        )

    async def on_plan_edited(self, mid=None, plan=None, author_id=None, thread_id=None,
                             thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody edits a plan

        :param mid: The action ID
        :param plan: Edited plan
        :param author_id: The ID of the person who edited the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} edited plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    async def on_plan_deleted(self, mid=None, plan=None, author_id=None, thread_id=None,
                              thread_type=None, at=None, metadata=None, msg=None) -> None:
        """
        Called when the client is listening, and somebody deletes a plan

        :param mid: The action ID
        :param plan: Deleted plan
        :param author_id: The ID of the person who deleted the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} deleted plan {} in {} ({})".format(
                author_id, plan, thread_id, thread_type.name
            )
        )

    async def on_plan_participation(self, mid=None, plan=None, take_part=None, author_id=None,
                                    thread_id=None, thread_type=None, at=None, metadata=None,
                                    msg=None) -> None:
        """
        Called when the client is listening, and somebody takes part in a plan or not

        :param mid: The action ID
        :param plan: Plan
        :param take_part: Whether the person takes part in the plan or not
        :param author_id: The ID of the person who will participate in the plan or not
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param at: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type take_part: bool
        :type thread_type: models.ThreadType
        """
        if take_part:
            self.log.info(
                "{} will take part in {} in {} ({})".format(
                    author_id, plan, thread_id, thread_type.name
                )
            )
        else:
            self.log.info(
                "{} won't take part in {} in {} ({})".format(
                    author_id, plan, thread_id, thread_type.name
                )
            )

    async def on_qprimer(self, at=None, msg=None) -> None:
        """
        Called when the client just started listening

        :param at: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        pass

    async def on_chat_timestamp(self, buddylist = None, msg: Any = None
                                ) -> None:
        """
        Called when the client receives chat online presence update

        :param buddylist: A list of dicts with friend id and last seen timestamp
        :param msg: A full set of the data recieved
        """
        for user, status in buddylist.items():
            puppet = pu.Puppet.get_by_fbid(user, create=False)
            if puppet:
                await puppet.default_mxid_intent.set_presence(
                    presence=PresenceState.ONLINE if status.active else PresenceState.OFFLINE,
                    ignore_cache=True)

    async def on_buddylist_overlay(self, statuses = None, msg: Any = None
                                   ) -> None:
        """
        Called when the client is listening and client receives information about friend active status

        :param statuses: Dictionary with user IDs as keys and :class:`models.ActiveStatus` as values
        :param msg: A full set of the data recieved
        :type statuses: dict
        """
        await self.on_chat_timestamp(statuses, msg)

    async def on_unknown_messsage_type(self, msg: Any = None) -> None:
        """
        Called when the client is listening, and some unknown data was recieved

        :param msg: A full set of the data recieved
        """
        self.log.debug("Unknown message received: {}".format(msg))

    async def on_message_error(self, exception: Exception = None, msg: Any = None) -> None:
        """
        Called when an error was encountered while parsing recieved data

        :param exception: The exception that was encountered
        :param msg: A full set of the data recieved
        """
        self.log.exception("Exception in parsing of {}".format(msg))

    # endregion


def init(context: 'Context') -> Iterable[Awaitable[bool]]:
    global config
    User.az, config, User.loop = context.core
    User._community_helper = CommunityHelper(User.az)
    return (user.load_session() for user in User.get_all())
