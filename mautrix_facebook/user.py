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
from typing import Any, Dict, Iterator, Optional, TYPE_CHECKING
import asyncio
import logging
import pickle
import os

from fbchat import Client, Message, ThreadType, User as FBUser
from mautrix.types import UserID
from mautrix.appservice import AppService

from .config import Config
from .commands import enter_2fa_code
from . import portal as po, puppet as pu

if TYPE_CHECKING:
    from .context import Context

config: Config


class User(Client):
    az: AppService
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.user")
    by_mxid: Dict[UserID, 'User'] = {}

    command_status: Optional[Dict[str, Any]]
    is_whitelisted: bool
    is_admin: bool
    _is_logged_in: Optional[bool]

    def __init__(self, mxid: UserID):
        super(User, self).__init__(loop=self.loop)
        self.log = self.log.getChild(mxid)
        self.mxid = mxid
        self.by_mxid[mxid] = self
        self.command_status = None
        self.is_whitelisted, self.is_admin = config.get_permissions(mxid)
        self._is_logged_in = None
        #self.setActiveStatus(False)

    # region Sessions

    def save(self) -> None:
        session = self.getSession()
        with open(f"{self.mxid}.session", "wb") as file:
            pickle.dump(session, file)

    async def load(self) -> bool:
        try:
            with open(f"{self.mxid}.session", "rb") as file:
                session = pickle.load(file)
        except FileNotFoundError:
            return False
        ok = await self.setSession(session) and await self.is_logged_in()
        if ok:
            self.listen()
            asyncio.ensure_future(self.sync_threads(), loop=self.loop)
        return ok

    @staticmethod
    def get_sessions() -> Iterator['User']:
        for file in os.listdir("."):
            if file.endswith(".session"):
                yield User(UserID(file[:-len(".session")]))

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True) -> 'User':
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            return cls(mxid) if create else None

    async def is_logged_in(self) -> bool:
        if self._is_logged_in is None:
            self._is_logged_in = await self.isLoggedIn()
        return self._is_logged_in

    # endregion

    async def sync_threads(self) -> None:
        try:
            self.log.debug("Fetching threads...")
            threads = await self.fetchThreadList(limit=10)
            for thread in threads:
                self.log.debug(f"Syncing thread {thread.uid} {thread.name}")
                fb_receiver = self.uid if thread.type == ThreadType.USER else None
                portal = po.Portal.get_by_thread(thread, fb_receiver)
                await portal.create_matrix_room(self, thread)
                if isinstance(thread, FBUser):
                    puppet = pu.Puppet.get(thread.uid, create=True)
                    await puppet.update_info(self, thread)
        except Exception:
            self.log.exception("Failed to sync threads")

    # region Facebook event handling

    async def onLoggingIn(self, email: str = None) -> None:
        self.log.info("Logging in {}...".format(email))

    async def on2FACode(self) -> str:
        if self.command_status and self.command_status.get("action", "") == "Login":
            future = self.loop.create_future()
            self.command_status["future"] = future
            self.command_status["next"] = enter_2fa_code
            await self.az.intent.send_notice(self.command_status["room_id"],
                                             "You have two-factor authentication enabled. "
                                             "Please send the code here.")
            return await future
        self.log.warn("Unexpected on2FACode call")
        # raise RuntimeError("No ongoing login command")

    async def onLoggedIn(self, email=None) -> None:
        """
        Called when the client is successfully logged in

        :param email: The email of the client
        """
        if self.command_status and self.command_status.get("action", "") == "Login":
            await self.az.intent.send_notice(self.command_status["room_id"],
                                             f"Successfully logged in with {email}")
            self.save()
            self.listen()
        self.log.warn("Unexpected onLoggedIn call")
        # raise RuntimeError("No ongoing login command")

    async def onListening(self) -> None:
        """Called when the client is listening"""
        self.log.info("Listening...")

    async def onListenError(self, exception: Exception = None) -> bool:
        """
        Called when an error was encountered while listening

        :param exception: The exception that was encountered
        :return: Whether the loop should keep running
        """
        self.log.exception("Got exception while listening")
        return True

    async def onMessage(self, mid: str = None, author_id: str = None, message: str = None,
                        message_object: Message = None, thread_id: str = None,
                        thread_type: ThreadType = ThreadType.USER, ts: int = None,
                        metadata: Any = None, msg: Any = None):
        """
        Called when the client is listening, and somebody sends a message

        :param mid: The message ID
        :param author_id: The ID of the author
        :param message: (deprecated. Use `message_object.text` instead)
        :param message_object: The message (As a `Message` object)
        :param thread_id: Thread ID that the message was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the message was sent to. See :ref:`intro_threads`
        :param ts: The timestamp of the message
        :param metadata: Extra metadata about the message
        :param msg: A full set of the data recieved
        :type message_object: models.Message
        :type thread_type: models.ThreadType
        """
        if author_id == self.uid:
            self.log.debug(f"Ignoring message from self ({mid}, {author_id}, {message}, "
                           f"{thread_id}, {thread_type})")
            return
        self.log.debug(f"onMessage({mid}, {author_id}, {message}, {thread_id}, {thread_type})")
        fb_receiver = self.uid if thread_type == ThreadType.USER else None
        portal = po.Portal.get_by_fbid(thread_id, fb_receiver, thread_type)
        puppet = pu.Puppet.get(author_id)
        if not puppet.name:
            await puppet.update_info(self)
        message_object.uid = mid
        await portal.handle_facebook_message(self, puppet, message_object)

    async def onColorChange(self, mid=None, author_id=None, new_color=None, thread_id=None,
                            thread_type=ThreadType.USER, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody changes a thread's color

        :param mid: The action ID
        :param author_id: The ID of the person who changed the color
        :param new_color: The new color
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type new_color: models.ThreadColor
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Color change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_color
            )
        )

    async def onEmojiChange(self, mid=None, author_id=None, new_emoji=None, thread_id=None,
                            thread_type=ThreadType.USER, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody changes a thread's emoji

        :param mid: The action ID
        :param author_id: The ID of the person who changed the emoji
        :param new_emoji: The new emoji
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Emoji change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_emoji
            )
        )

    async def onTitleChange(self, mid=None, author_id=None, new_title=None, thread_id=None,
                            thread_type=ThreadType.USER, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody changes the title of a thread

        :param mid: The action ID
        :param author_id: The ID of the person who changed the title
        :param new_title: The new title
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Title change from {} in {} ({}): {}".format(
                author_id, thread_id, thread_type.name, new_title
            )
        )

    async def onImageChange(self, mid=None, author_id=None, new_image=None, thread_id=None,
                            thread_type=ThreadType.GROUP, ts=None, msg=None):
        """
        Called when the client is listening, and somebody changes the image of a thread

        :param mid: The action ID
        :param author_id: The ID of the person who changed the image
        :param new_image: The ID of the new image
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info("{} changed thread image in {}".format(author_id, thread_id))

    async def onNicknameChange(self, mid=None, author_id=None, changed_for=None, new_nickname=None,
                               thread_id=None, thread_type=ThreadType.USER, ts=None, metadata=None,
                               msg=None):
        """
        Called when the client is listening, and somebody changes the nickname of a person

        :param mid: The action ID
        :param author_id: The ID of the person who changed the nickname
        :param changed_for: The ID of the person whom got their nickname changed
        :param new_nickname: The new nickname
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Nickname change from {} in {} ({}) for {}: {}".format(
                author_id, thread_id, thread_type.name, changed_for, new_nickname
            )
        )

    async def onAdminAdded(self, mid=None, added_id=None, author_id=None, thread_id=None,
                           thread_type=ThreadType.GROUP, ts=None, msg=None):
        """
        Called when the client is listening, and somebody adds an admin to a group thread

        :param mid: The action ID
        :param added_id: The ID of the admin who got added
        :param author_id: The ID of the person who added the admins
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} added admin: {} in {}".format(author_id, added_id, thread_id))

    async def onAdminRemoved(self, mid=None, removed_id=None, author_id=None, thread_id=None,
                             thread_type=ThreadType.GROUP, ts=None, msg=None):
        """
        Called when the client is listening, and somebody removes an admin from a group thread

        :param mid: The action ID
        :param removed_id: The ID of the admin who got removed
        :param author_id: The ID of the person who removed the admins
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} removed admin: {} in {}".format(author_id, removed_id, thread_id))

    async def onApprovalModeChange(self, mid=None, approval_mode=None, author_id=None,
                                   thread_id=None, thread_type=ThreadType.GROUP, ts=None, msg=None):
        """
        Called when the client is listening, and somebody changes approval mode in a group thread

        :param mid: The action ID
        :param approval_mode: True if approval mode is activated
        :param author_id: The ID of the person who changed approval mode
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        if approval_mode:
            self.log.info("{} activated approval mode in {}".format(author_id, thread_id))
        else:
            self.log.info("{} disabled approval mode in {}".format(author_id, thread_id))

    async def onMessageSeen(self, seen_by=None, thread_id=None, thread_type=ThreadType.USER,
                            seen_ts=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody marks a message as seen

        :param seen_by: The ID of the person who marked the message as seen
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param seen_ts: A timestamp of when the person saw the message
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Messages seen by {} in {} ({}) at {}s".format(
                seen_by, thread_id, thread_type.name, seen_ts / 1000
            )
        )

    async def onMessageDelivered(self, msg_ids=None, delivered_for=None, thread_id=None,
                                 thread_type=ThreadType.USER, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody marks messages as delivered

        :param msg_ids: The messages that are marked as delivered
        :param delivered_for: The person that marked the messages as delivered
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Messages {} delivered to {} in {} ({}) at {}s".format(
                msg_ids, delivered_for, thread_id, thread_type.name, ts / 1000
            )
        )

    async def onMarkedSeen(self, threads=None, seen_ts=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and the client has successfully marked threads as seen

        :param threads: The threads that were marked
        :param author_id: The ID of the person who changed the emoji
        :param seen_ts: A timestamp of when the threads were seen
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Marked messages as seen in threads {} at {}s".format(
                [(x[0], x[1].name) for x in threads], seen_ts / 1000
            )
        )

    async def onMessageUnsent(self, mid=None, author_id=None, thread_id=None, thread_type=None,
                              ts=None, msg=None):
        """
        Called when the client is listening, and someone unsends (deletes for everyone) a message

        :param mid: ID of the unsent message
        :param author_id: The ID of the person who unsent the message
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} unsent the message {} in {} ({}) at {}s".format(
                author_id, repr(mid), thread_id, thread_type.name, ts / 1000
            )
        )

    async def onPeopleAdded(self, mid=None, added_ids=None, author_id=None, thread_id=None, ts=None,
                            msg=None):
        """
        Called when the client is listening, and somebody adds people to a group thread

        :param mid: The action ID
        :param added_ids: The IDs of the people who got added
        :param author_id: The ID of the person who added the people
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info(
            "{} added: {} in {}".format(author_id, ", ".join(added_ids), thread_id)
        )

    async def onPersonRemoved(self, mid=None, removed_id=None, author_id=None, thread_id=None,
                              ts=None, msg=None):
        """
        Called when the client is listening, and somebody removes a person from a group thread

        :param mid: The action ID
        :param removed_id: The ID of the person who got removed
        :param author_id: The ID of the person who removed the person
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        self.log.info("{} removed: {} in {}".format(author_id, removed_id, thread_id))

    async def onFriendRequest(self, from_id=None, msg=None):
        """
        Called when the client is listening, and somebody sends a friend request

        :param from_id: The ID of the person that sent the request
        :param msg: A full set of the data recieved
        """
        self.log.info("Friend request from {}".format(from_id))

    async def onInbox(self, unseen=None, unread=None, recent_unread=None, msg=None):
        """
        .. todo::
            Documenting this

        :param unseen: --
        :param unread: --
        :param recent_unread: --
        :param msg: A full set of the data recieved
        """
        self.log.info("Inbox event: {}, {}, {}".format(unseen, unread, recent_unread))

    async def onTyping(self, author_id=None, status=None, thread_id=None, thread_type=None,
                       msg=None):
        """
        Called when the client is listening, and somebody starts or stops typing into a chat

        :param author_id: The ID of the person who sent the action
        :param status: The typing status
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param msg: A full set of the data recieved
        :type typing_status: models.TypingStatus
        :type thread_type: models.ThreadType
        """
        pass

    async def onGamePlayed(self, mid=None, author_id=None, game_id=None, game_name=None, score=None,
                           leaderboard=None, thread_id=None, thread_type=None, ts=None,
                           metadata=None, msg=None):
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
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            '{} played "{}" in {} ({})'.format(
                author_id, game_name, thread_id, thread_type.name
            )
        )

    async def onReactionAdded(self, mid=None, reaction=None, author_id=None, thread_id=None,
                              thread_type=None, ts=None, msg=None):
        """
        Called when the client is listening, and somebody reacts to a message

        :param mid: Message ID, that user reacted to
        :param reaction: Reaction
        :param add_reaction: Whether user added or removed reaction
        :param author_id: The ID of the person who reacted to the message
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type reaction: models.MessageReaction
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} reacted to message {} with {} in {} ({})".format(
                author_id, mid, reaction.name, thread_id, thread_type.name
            )
        )

    async def onReactionRemoved(self, mid=None, author_id=None, thread_id=None, thread_type=None,
                                ts=None, msg=None):
        """
        Called when the client is listening, and somebody removes reaction from a message

        :param mid: Message ID, that user reacted to
        :param author_id: The ID of the person who removed reaction
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} removed reaction from {} message in {} ({})".format(
                author_id, mid, thread_id, thread_type
            )
        )

    async def onBlock(self, author_id=None, thread_id=None, thread_type=None, ts=None, msg=None):
        """
        Called when the client is listening, and somebody blocks client

        :param author_id: The ID of the person who blocked
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} blocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    async def onUnblock(self, author_id=None, thread_id=None, thread_type=None, ts=None, msg=None):
        """
        Called when the client is listening, and somebody blocks client

        :param author_id: The ID of the person who unblocked
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} unblocked {} ({}) thread".format(author_id, thread_id, thread_type.name)
        )

    async def onLiveLocation(self, mid=None, location=None, author_id=None, thread_id=None,
                             thread_type=None, ts=None, msg=None, ):
        """
        Called when the client is listening and somebody sends live location info

        :param mid: The action ID
        :param location: Sent location info
        :param author_id: The ID of the person who sent location info
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        :type location: models.LiveLocationAttachment
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} sent live location info in {} ({}) with latitude {} and longitude {}".format(
                author_id, thread_id, thread_type, location.latitude, location.longitude
            )
        )

    async def onCallStarted(self, mid=None, caller_id=None, is_video_call=None, thread_id=None,
                            thread_type=None, ts=None, metadata=None, msg=None):
        """
        .. todo::
            Make this work with private calls

        Called when the client is listening, and somebody starts a call in a group

        :param mid: The action ID
        :param caller_id: The ID of the person who started the call
        :param is_video_call: True if it's video call
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} started call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    async def onCallEnded(self, mid=None, caller_id=None, is_video_call=None, call_duration=None,
                          thread_id=None, thread_type=None, ts=None, metadata=None, msg=None):
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
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} ended call in {} ({})".format(caller_id, thread_id, thread_type.name)
        )

    async def onUserJoinedCall(self, mid=None, joined_id=None, is_video_call=None, thread_id=None,
                               thread_type=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody joins a group call

        :param mid: The action ID
        :param joined_id: The ID of the person who joined the call
        :param is_video_call: True if it's video call
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "{} joined call in {} ({})".format(joined_id, thread_id, thread_type.name)
        )

    async def onPollCreated(self, mid=None, poll=None, author_id=None, thread_id=None,
                            thread_type=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody creates a group poll

        :param mid: The action ID
        :param poll: Created poll
        :param author_id: The ID of the person who created the poll
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onPollVoted(self, mid=None, poll=None, added_options=None, removed_options=None,
                          author_id=None, thread_id=None, thread_type=None, ts=None, metadata=None,
                          msg=None):
        """
        Called when the client is listening, and somebody votes in a group poll

        :param mid: The action ID
        :param poll: Poll, that user voted in
        :param author_id: The ID of the person who voted in the poll
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onPlanCreated(self, mid=None, plan=None, author_id=None, thread_id=None,
                            thread_type=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody creates a plan

        :param mid: The action ID
        :param plan: Created plan
        :param author_id: The ID of the person who created the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onPlanEnded(self, mid=None, plan=None, thread_id=None, thread_type=None, ts=None,
                          metadata=None, msg=None):
        """
        Called when the client is listening, and a plan ends

        :param mid: The action ID
        :param plan: Ended plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
        :param metadata: Extra metadata about the action
        :param msg: A full set of the data recieved
        :type plan: models.Plan
        :type thread_type: models.ThreadType
        """
        self.log.info(
            "Plan {} has ended in {} ({})".format(plan, thread_id, thread_type.name)
        )

    async def onPlanEdited(self, mid=None, plan=None, author_id=None, thread_id=None,
                           thread_type=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody edits a plan

        :param mid: The action ID
        :param plan: Edited plan
        :param author_id: The ID of the person who edited the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onPlanDeleted(self, mid=None, plan=None, author_id=None, thread_id=None,
                            thread_type=None, ts=None, metadata=None, msg=None):
        """
        Called when the client is listening, and somebody deletes a plan

        :param mid: The action ID
        :param plan: Deleted plan
        :param author_id: The ID of the person who deleted the plan
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onPlanParticipation(self, mid=None, plan=None, take_part=None, author_id=None,
                                  thread_id=None, thread_type=None, ts=None, metadata=None,
                                  msg=None):
        """
        Called when the client is listening, and somebody takes part in a plan or not

        :param mid: The action ID
        :param plan: Plan
        :param take_part: Whether the person takes part in the plan or not
        :param author_id: The ID of the person who will participate in the plan or not
        :param thread_id: Thread ID that the action was sent to. See :ref:`intro_threads`
        :param thread_type: Type of thread that the action was sent to. See :ref:`intro_threads`
        :param ts: A timestamp of the action
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

    async def onQprimer(self, ts=None, msg=None):
        """
        Called when the client just started listening

        :param ts: A timestamp of the action
        :param msg: A full set of the data recieved
        """
        pass

    async def onChatTimestamp(self, buddylist=None, msg=None):
        """
        Called when the client receives chat online presence update

        :param buddylist: A list of dicts with friend id and last seen timestamp
        :param msg: A full set of the data recieved
        """
        self.log.debug("Chat Timestamps received: {}".format(buddylist))

    async def onBuddylistOverlay(self, statuses=None, msg=None):
        """
        Called when the client is listening and client receives information about friend active status

        :param statuses: Dictionary with user IDs as keys and :class:`models.ActiveStatus` as values
        :param msg: A full set of the data recieved
        :type statuses: dict
        """
        self.log.debug("Buddylist overlay received: {}".format(statuses))

    async def onUnknownMesssageType(self, msg=None):
        """
        Called when the client is listening, and some unknown data was recieved

        :param msg: A full set of the data recieved
        """
        self.log.debug("Unknown message received: {}".format(msg))

    async def onMessageError(self, exception=None, msg=None):
        """
        Called when an error was encountered while parsing recieved data

        :param exception: The exception that was encountered
        :param msg: A full set of the data recieved
        """
        self.log.exception("Exception in parsing of {}".format(msg))

    # endregion


def init(context: 'Context') -> None:
    global config
    User.az, config, User.loop = context.core
