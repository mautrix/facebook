# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2023 Tulir Asokan
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

from typing import TYPE_CHECKING, Any, AsyncGenerator, Awaitable, Literal, Pattern, Tuple, cast
from collections import deque
from html import escape
from io import BytesIO
import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import time

from yarl import URL

from maufbapi.http.errors import RateLimitExceeded
from maufbapi.types import graphql, mqtt
from mautrix.appservice import DOUBLE_PUPPET_SOURCE_KEY, IntentAPI
from mautrix.bridge import BasePortal, async_getter_lock
from mautrix.errors import DecryptionError, IntentError, MatrixError, MForbidden, MNotFound
from mautrix.types import (
    AudioInfo,
    BatchID,
    BatchSendEvent,
    BatchSendStateEvent,
    BeeperMessageStatusEventContent,
    ContentURI,
    EncryptedFile,
    EventID,
    EventType,
    FileInfo,
    Format,
    ImageInfo,
    LocationMessageEventContent,
    MediaMessageEventContent,
    Membership,
    MemberStateEventContent,
    MessageEventContent,
    MessageStatus,
    MessageStatusReason,
    MessageType,
    ReactionEventContent,
    RelatesTo,
    RelationType,
    RoomID,
    TextMessageEventContent,
    UserID,
    VideoInfo,
)
from mautrix.util import background_task, ffmpeg, magic, variation_selector
from mautrix.util.formatter import parse_html
from mautrix.util.message_send_checkpoint import MessageSendCheckpointStatus

from . import matrix as m, puppet as p, user as u
from .config import Config
from .db import (
    Backfill,
    Message as DBMessage,
    Portal as DBPortal,
    Reaction as DBReaction,
    ThreadType,
    UserPortal as UserPortal,
)
from .formatter import facebook_to_matrix, matrix_to_facebook
from .segment_analytics import track

if TYPE_CHECKING:
    from .__main__ import MessengerBridge

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from mautrix.crypto.attachments import decrypt_attachment, encrypt_attachment
except ImportError:
    decrypt_attachment = encrypt_attachment = None

geo_uri_regex: Pattern = re.compile(r"^geo:(-?\d+.\d+),(-?\d+.\d+)$")


class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


StateBridge = EventType.find("m.bridge", EventType.Class.STATE)
StateHalfShotBridge = EventType.find("uk.half-shot.bridge", EventType.Class.STATE)

PortalCreateDummy = EventType.find("fi.mau.dummy.portal_created", EventType.Class.MESSAGE)
HistorySyncMarkerMessage = EventType.find("org.matrix.msc2716.marker", EventType.Class.MESSAGE)

ConvertedMessage = Tuple[EventType, MessageEventContent]


class Portal(DBPortal, BasePortal):
    invite_own_puppet_to_pm: bool = False
    by_mxid: dict[RoomID, Portal] = {}
    by_fbid: dict[tuple[int, int], Portal] = {}
    matrix: m.MatrixHandler
    config: Config
    private_chat_portal_meta: Literal["default", "always", "never"]
    disable_reply_fallbacks: bool

    _main_intent: IntentAPI | None
    _create_room_lock: asyncio.Lock
    _dedup: deque[str]
    _oti_dedup: dict[int, DBMessage]
    _send_locks: dict[int, asyncio.Lock]
    _noop_lock: FakeLock = FakeLock()
    _typing: set[UserID]
    _sleeping_to_resync: bool
    _scheduled_resync: asyncio.Task | None
    _resync_targets: dict[int, p.Puppet]

    def __init__(
        self,
        fbid: int,
        fb_receiver: int,
        fb_type: ThreadType,
        mxid: RoomID | None = None,
        name: str | None = None,
        photo_id: str | None = None,
        avatar_url: ContentURI | None = None,
        encrypted: bool = False,
        name_set: bool = False,
        avatar_set: bool = False,
        relay_user_id: UserID | None = None,
        first_event_id: EventID | None = None,
        next_batch_id: BatchID | None = None,
        historical_base_insertion_event_id: EventID | None = None,
    ) -> None:
        super().__init__(
            fbid,
            fb_receiver,
            fb_type,
            mxid,
            name,
            photo_id,
            avatar_url,
            encrypted,
            name_set,
            avatar_set,
            relay_user_id,
            first_event_id,
            next_batch_id,
            historical_base_insertion_event_id,
        )
        self.log = self.log.getChild(self.fbid_log)

        self._main_intent = None
        self._create_room_lock = asyncio.Lock()
        self._dedup = deque(maxlen=100)
        self._oti_dedup = {}
        self._send_locks = {}
        self._typing = set()
        self._sleeping_to_resync = False
        self._scheduled_resync = None
        self._resync_targets = {}
        self._relay_user = None

    @classmethod
    def init_cls(cls, bridge: "MessengerBridge") -> None:
        BasePortal.bridge = bridge
        cls.az = bridge.az
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.matrix = bridge.matrix
        cls.invite_own_puppet_to_pm = cls.config["bridge.invite_own_puppet_to_pm"]
        cls.private_chat_portal_meta = cls.config["bridge.private_chat_portal_meta"]
        cls.disable_reply_fallbacks = cls.config["bridge.disable_reply_fallbacks"]

    # region DB conversion

    async def delete(self) -> None:
        if self.mxid:
            await DBMessage.delete_all_by_room(self.mxid)
            await DBReaction.delete_all_by_room(self.mxid)
            self.by_mxid.pop(self.mxid, None)
        await Backfill.delete_for_portal(self.fbid, self.fb_receiver)
        self.by_fbid.pop(self.fbid_full, None)
        self.mxid = None
        self.name_set = False
        self.avatar_set = False
        self.relay_user_id = None
        self.encrypted = False
        self.first_event_id = None
        self.next_batch_id = None
        await super().save()

    # endregion
    # region Properties

    @property
    def fbid_full(self) -> tuple[int, int]:
        return self.fbid, self.fb_receiver

    @property
    def fbid_log(self) -> str:
        if self.is_direct:
            return f"{self.fbid}<->{self.fb_receiver}"
        return str(self.fbid)

    @property
    def mqtt_key(self) -> mqtt.ThreadKey:
        if self.fb_type == ThreadType.USER:
            return mqtt.ThreadKey(other_user_id=self.fbid)
        elif self.fb_type == ThreadType.GROUP:
            return mqtt.ThreadKey(thread_fbid=self.fbid)
        else:
            raise ValueError("Unsupported thread type")

    @property
    def graphql_key(self) -> graphql.ThreadKey:
        if self.fb_type == ThreadType.USER:
            return graphql.ThreadKey(other_user_id=str(self.fbid))
        elif self.fb_type == ThreadType.GROUP:
            return graphql.ThreadKey(thread_fbid=str(self.fbid))
        else:
            raise ValueError("Unsupported thread type")

    @property
    def set_dm_room_metadata(self) -> bool:
        return (
            not self.is_direct
            or self.private_chat_portal_meta == "always"
            or (self.encrypted and self.private_chat_portal_meta != "never")
        )

    @property
    def is_direct(self) -> bool:
        return self.fb_type == ThreadType.USER

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            raise ValueError("Portal must be postinit()ed before main_intent can be used")
        return self._main_intent

    async def get_dm_puppet(self) -> p.Puppet | None:
        if not self.is_direct:
            return None
        return await p.Puppet.get_by_fbid(self.fbid)

    # endregion
    # region Chat info updating

    def schedule_resync(self, source: u.User, target: p.Puppet) -> None:
        self._resync_targets[target.fbid] = target
        if (
            self._sleeping_to_resync
            and self._scheduled_resync
            and not self._scheduled_resync.done()
        ):
            return
        self._sleeping_to_resync = True
        self.log.debug(f"Scheduling resync through {source.mxid}/{source.fbid}")
        self._scheduled_resync = asyncio.create_task(self._sleep_and_resync(source, 10))

    async def _sleep_and_resync(self, source: u.User, sleep: int) -> None:
        await asyncio.sleep(sleep)
        targets = self._resync_targets
        self._sleeping_to_resync = False
        self._resync_targets = {}
        for puppet in targets.values():
            if not puppet.name or not puppet.name_set:
                break
        else:
            self.log.debug(
                f"Cancelled resync through {source.mxid}/{source.fbid}, all puppets have names"
            )
            return
        self.log.debug(f"Resyncing chat through {source.mxid}/{source.fbid} after sleeping")
        await self.update_info(source)
        self._scheduled_resync = None
        self.log.debug(f"Completed scheduled resync through {source.mxid}/{source.fbid}")

    async def update_info(
        self,
        source: u.User,
        info: graphql.Thread | None = None,
        force_save: bool = False,
    ) -> graphql.Thread | None:
        if not info:
            self.log.debug("Called update_info with no info, fetching thread info...")
            threads = await source.client.fetch_thread_info(self.fbid)
            if not threads:
                return None
            elif threads[0].thread_key.id != self.fbid:
                self.log.warning(
                    "fetch_thread_info response contained different ID (%s) than expected (%s)",
                    threads[0].thread_key.id,
                    self.fbid,
                )
                self.log.debug(f"Number of threads in unexpected response: {len(threads)}")
            info = threads[0]
        if info.thread_key != self.graphql_key:
            self.log.warning(
                "Got different ID (%s) than what asked for (%s) when fetching info",
                info.thread_key.id,
                self.fbid,
            )
        changed = False
        if not self.is_direct:
            changed = any(
                await asyncio.gather(
                    self._update_name(info.name),
                    self._update_photo(source, info.image),
                )
            )
        changed = await self._update_participants(source, info) or changed
        if changed or force_save:
            await self.update_bridge_info()
            await self.save()
        return info

    @staticmethod
    def get_photo_id(photo: graphql.Picture | str | None) -> str | None:
        if not photo:
            return None
        elif isinstance(photo, graphql.Picture):
            photo = photo.uri
        path = URL(photo).path
        return path[path.rfind("/") + 1 :]

    @classmethod
    async def _reupload_fb_file(
        cls,
        url: str,
        source: u.User,
        intent: IntentAPI,
        *,
        filename: str | None = None,
        encrypt: bool = False,
        referer: str = "messenger_thread_photo",
        find_size: bool = False,
        convert_audio: bool = False,
    ) -> tuple[ContentURI, FileInfo | VideoInfo | AudioInfo | ImageInfo, EncryptedFile | None]:
        if not url:
            raise ValueError("URL not provided")
        headers = {"referer": f"fbapp://{source.state.application.client_id}/{referer}"}
        sandbox = cls.config["bridge.sandbox_media_download"]
        cls.log.trace("Reuploading file %s", url)
        async with source.client.raw_http_get(url, headers=headers, sandbox=sandbox) as resp:
            length = int(resp.headers["Content-Length"])
            if length > cls.matrix.media_config.upload_size:
                raise ValueError("File not available: too large")
            data = await resp.read()
        mime = magic.mimetype(data)
        if convert_audio and mime != "audio/ogg":
            data = await ffmpeg.convert_bytes(
                data, ".ogg", output_args=("-c:a", "libopus"), input_mime=mime
            )
            mime = "audio/ogg"
        info = FileInfo(mimetype=mime, size=len(data))
        if Image and mime.startswith("image/") and find_size:
            with Image.open(BytesIO(data)) as img:
                width, height = img.size
            info = ImageInfo(mimetype=mime, size=len(data), width=width, height=height)
        upload_mime_type = mime
        decryption_info = None
        if encrypt and encrypt_attachment:
            data, decryption_info = encrypt_attachment(data)
            upload_mime_type = "application/octet-stream"
            filename = None
        url = await intent.upload_media(
            data,
            mime_type=upload_mime_type,
            filename=filename,
            async_upload=cls.config["homeserver.async_media"],
        )
        if decryption_info:
            decryption_info.url = url
        return url, info, decryption_info

    async def _update_name(self, name: str | None) -> bool:
        if not name:
            self.log.warning("Got empty name in _update_name call")
            return False
        if self.name != name or (not self.name_set and self.set_dm_room_metadata):
            self.log.trace("Updating name %s -> %s", self.name, name)
            self.name = name
            self.name_set = False
            if self.mxid and self.set_dm_room_metadata:
                try:
                    await self.main_intent.set_room_name(self.mxid, self.name)
                    self.name_set = True
                except Exception:
                    self.log.exception("Failed to set room name")
            return True
        return False

    async def _update_photo(self, source: u.User, photo: graphql.Picture | None) -> bool:
        photo_id = self.get_photo_id(photo)
        if self.photo_id != photo_id or not self.avatar_set:
            self.photo_id = photo_id
            self.avatar_set = False
            if photo:
                if self.photo_id != photo_id or not self.avatar_url:
                    # Reset avatar_url first in case the upload fails
                    self.avatar_url = None
                    self.avatar_url = await p.Puppet.reupload_avatar(
                        source,
                        self.main_intent,
                        photo.uri,
                        self.fbid,
                        use_graph=self.is_direct and (photo.height or 0) < 500,
                    )
            else:
                self.avatar_url = ContentURI("")
            if self.mxid:
                try:
                    await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
                    self.avatar_set = True
                except Exception:
                    self.log.exception("Failed to set room avatar")
            return True
        return False

    async def _update_photo_from_puppet(self, puppet: p.Puppet) -> bool:
        if self.photo_id == puppet.photo_id and (self.avatar_set or not self.set_dm_room_metadata):
            return False
        self.photo_id = puppet.photo_id
        if puppet.photo_mxc:
            self.avatar_url = puppet.photo_mxc
        elif self.photo_id:
            profile = await self.main_intent.get_profile(puppet.default_mxid)
            self.avatar_url = profile.avatar_url
            puppet.photo_mxc = profile.avatar_url
        else:
            self.avatar_url = ContentURI("")
        self.avatar_set = False
        if self.mxid and self.set_dm_room_metadata:
            try:
                await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
                self.avatar_set = True
            except Exception:
                self.log.exception("Failed to set room avatar")
        return True

    async def update_info_from_puppet(self, puppet: p.Puppet | None = None) -> bool:
        if not self.is_direct:
            return False
        if not puppet:
            puppet = await self.get_dm_puppet()
        changed = await self._update_name(puppet.name)
        changed = await self._update_photo_from_puppet(puppet) or changed
        return changed

    async def sync_per_room_nick(self, puppet: p.Puppet, name: str) -> None:
        intent = puppet.intent_for(self)
        content = MemberStateEventContent(
            membership=Membership.JOIN,
            avatar_url=puppet.photo_mxc,
            displayname=name or puppet.name,
        )
        content[DOUBLE_PUPPET_SOURCE_KEY] = self.bridge.name
        current_state = await intent.state_store.get_member(self.mxid, intent.mxid)
        if not current_state or current_state.displayname != content.displayname:
            self.log.debug(
                "Syncing %s's per-room nick %s to the room",
                puppet.fbid,
                content.displayname,
            )
            await intent.send_state_event(
                self.mxid, EventType.ROOM_MEMBER, content, state_key=intent.mxid
            )

    async def _update_participant(
        self, source: u.User, participant: graphql.ParticipantNode, nick_map: dict[int, str]
    ) -> bool:
        self.log.trace("Syncing participant %s", participant.id)
        puppet = await p.Puppet.get_by_fbid(int(participant.id))
        await puppet.update_info(source, participant.messaging_actor)
        changed = False
        if self.is_direct and self.fbid == puppet.fbid:
            changed = await self.update_info_from_puppet(puppet) or changed
        if self.mxid:
            if puppet.fbid != self.fb_receiver or puppet.is_real_user:
                await puppet.intent_for(self).ensure_joined(self.mxid, bot=self.main_intent)
            if puppet.fbid in nick_map and not puppet.is_real_user:
                await self.sync_per_room_nick(puppet, nick_map[puppet.fbid])
        return changed

    async def _update_participants(self, source: u.User, info: graphql.Thread) -> bool:
        nick_map = info.customization_info.nickname_map if info.customization_info else {}
        sync_tasks = [
            self._update_participant(source, pcp, nick_map) for pcp in info.all_participants.nodes
        ]
        changed = any(await asyncio.gather(*sync_tasks))
        return changed

    # endregion
    # region Matrix room creation

    async def update_matrix_room(self, source: u.User, info: graphql.Thread | None = None) -> None:
        try:
            await self._update_matrix_room(source, info)
        except Exception:
            self.log.exception("Failed to update portal")

    def _get_invite_content(self, double_puppet: p.Puppet | None) -> dict[str, Any]:
        invite_content = {}
        if double_puppet:
            invite_content["fi.mau.will_auto_accept"] = True
        if self.is_direct:
            invite_content["is_direct"] = True
        return invite_content

    async def _update_matrix_room(
        self, source: u.User, info: graphql.Thread | None = None
    ) -> None:
        puppet = await p.Puppet.get_by_custom_mxid(source.mxid)
        await self.main_intent.invite_user(
            self.mxid,
            source.mxid,
            check_cache=True,
            extra_content=self._get_invite_content(puppet),
        )
        if puppet:
            did_join = await puppet.intent.ensure_joined(self.mxid)
            if did_join and self.is_direct:
                await source.update_direct_chats({self.main_intent.mxid: [self.mxid]})

        info = await self.update_info(source, info)
        if not info:
            self.log.warning("Canceling _update_matrix_room as update_info didn't return info")
            return

        await UserPortal(
            user=source.fbid,
            portal=self.fbid,
            portal_receiver=self.fb_receiver,
        ).upsert()
        await self._sync_read_receipts(info.read_receipts.nodes, reactions=False)

    async def _sync_read_receipts(
        self, receipts: list[graphql.ReadReceipt], reactions: bool
    ) -> None:
        for receipt in receipts:
            if not receipt.actor:
                continue
            message = await DBMessage.get_closest_before(
                self.fbid, self.fb_receiver, receipt.timestamp
            )
            if not message:
                continue
            puppet = await p.Puppet.get_by_fbid(receipt.actor.id, create=False)
            if not puppet:
                continue
            msgid_text = message.mxid
            if reactions and message.fbid:
                reaction = await DBReaction.get_last_for_message(message.fbid, message.fb_receiver)
                if reaction:
                    msgid_text = f"{message.mxid} -> last reaction {reaction.mxid}"
                    message = reaction
            self.log.debug(
                "%s has read messages up to %d -> %s", puppet.mxid, receipt.timestamp, msgid_text
            )
            try:
                await puppet.intent_for(self).mark_read(message.mx_room, message.mxid)
            except Exception:
                self.log.warning(
                    f"Failed to mark {message.mxid} in {message.mx_room} "
                    f"as read by {puppet.intent.mxid}",
                    exc_info=True,
                )

    async def create_matrix_room(
        self, source: u.User, info: graphql.Thread | None = None
    ) -> RoomID | None:
        if self.mxid:
            try:
                await self._update_matrix_room(source, info)
            except Exception:
                self.log.exception("Failed to update portal")
            return self.mxid
        async with self._create_room_lock:
            try:
                return await self._create_matrix_room(source, info)
            except Exception:
                self.log.exception("Failed to create portal")
                return None

    @property
    def bridge_info_state_key(self) -> str:
        return f"net.maunium.facebook://facebook/{self.fbid}"

    @property
    def bridge_info(self) -> dict[str, Any]:
        return {
            "bridgebot": self.az.bot_mxid,
            "creator": self.main_intent.mxid,
            "protocol": {
                "id": "facebook",
                "displayname": "Facebook Messenger",
                "avatar_url": self.config["appservice.bot_avatar"],
            },
            "channel": {
                "id": str(self.fbid),
                "displayname": self.name,
                "avatar_url": self.avatar_url,
            },
        }

    async def update_bridge_info(self) -> None:
        if not self.mxid:
            self.log.debug("Not updating bridge info: no Matrix room created")
            return
        try:
            self.log.debug("Updating bridge info...")
            await self.main_intent.send_state_event(
                self.mxid, StateBridge, self.bridge_info, self.bridge_info_state_key
            )
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            await self.main_intent.send_state_event(
                self.mxid, StateHalfShotBridge, self.bridge_info, self.bridge_info_state_key
            )
        except Exception:
            self.log.warning("Failed to update bridge info", exc_info=True)

    async def _create_matrix_room(
        self, source: u.User, info: graphql.Thread | None = None
    ) -> RoomID | None:
        if self.mxid:
            await self._update_matrix_room(source, info)
            return self.mxid

        self.log.debug("Creating Matrix room")
        name: str | None = None
        initial_state = [
            {
                "type": str(StateBridge),
                "state_key": self.bridge_info_state_key,
                "content": self.bridge_info,
            },
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            {
                "type": str(StateHalfShotBridge),
                "state_key": self.bridge_info_state_key,
                "content": self.bridge_info,
            },
        ]
        invites = []
        if self.config["bridge.encryption.default"] and self.matrix.e2ee:
            self.encrypted = True
            initial_state.append(
                {
                    "type": "m.room.encryption",
                    "content": self.get_encryption_state_event_json(),
                }
            )
            if self.is_direct:
                invites.append(self.az.bot_mxid)

        info = await self.update_info(source, info=info)
        if not info:
            self.log.debug("update_info() didn't return info, cancelling room creation")
            return None

        if self.set_dm_room_metadata:
            name = self.name
            initial_state.append(
                {
                    "type": str(EventType.ROOM_AVATAR),
                    "content": {"url": self.avatar_url},
                }
            )

        creation_content = {}
        if not self.config["bridge.federate_rooms"]:
            creation_content["m.federate"] = False
        self.mxid = await self.main_intent.create_room(
            name=name,
            is_direct=self.is_direct,
            initial_state=initial_state,
            invitees=invites,
            creation_content=creation_content,
        )
        if not self.mxid:
            raise Exception("Failed to create room: no mxid returned")
        self.name_set = bool(name)
        self.avatar_set = bool(self.avatar_url) and self.set_dm_room_metadata

        if self.encrypted and self.matrix.e2ee and self.is_direct:
            try:
                await self.az.intent.ensure_joined(self.mxid)
            except Exception:
                self.log.warning(f"Failed to add bridge bot to new private chat {self.mxid}")
        await self.save()
        self.log.debug(f"Matrix room created: {self.mxid}")
        self.by_mxid[self.mxid] = self

        puppet = await p.Puppet.get_by_custom_mxid(source.mxid)
        await self.main_intent.invite_user(
            self.mxid, source.mxid, extra_content=self._get_invite_content(puppet)
        )
        if puppet:
            try:
                if self.is_direct:
                    await source.update_direct_chats({self.main_intent.mxid: [self.mxid]})
                await puppet.intent.join_room_by_id(self.mxid)
            except MatrixError:
                self.log.debug(
                    "Failed to join custom puppet into newly created portal",
                    exc_info=True,
                )

        if not self.is_direct:
            await self._update_participants(source, info)

        await UserPortal(
            user=source.fbid,
            portal=self.fbid,
            portal_receiver=self.fb_receiver,
        ).upsert()

        self.log.trace("Sending portal post-create dummy event")
        self.first_event_id = await self.main_intent.send_message_event(
            self.mxid, PortalCreateDummy, {}
        )
        await self.save()

        await self._sync_read_receipts(info.read_receipts.nodes, reactions=True)
        return self.mxid

    # endregion
    # region Backfill

    async def enqueue_immediate_backfill(self, source: u.User, priority: int) -> None:
        assert self.config["bridge.backfill.msc2716"]
        if not await Backfill.get(source.mxid, self.fbid, self.fb_receiver):
            await Backfill.new(
                source.mxid,
                priority,
                self.fbid,
                self.fb_receiver,
                self.config["bridge.backfill.incremental.max_pages"],
                self.config["bridge.backfill.incremental.page_delay"],
                self.config["bridge.backfill.incremental.post_batch_delay"],
                self.config["bridge.backfill.incremental.max_total_pages"],
            ).insert()

    async def backfill(self, source: u.User, backfill_request: Backfill) -> None:
        try:
            last_message_timestamp = await self._backfill(source, backfill_request)
            if (
                last_message_timestamp is not None
                and not self.bridge.homeserver_software.is_hungry
                and self.config["bridge.backfill.msc2716"]
            ):
                await self.send_post_backfill_dummy(last_message_timestamp)
        finally:
            # Always sleep after the backfill request is finished processing, even if it errors.
            await asyncio.sleep(backfill_request.post_batch_delay)

    async def _backfill(self, source: u.User, backfill_request: Backfill) -> int | None:
        assert source.client
        self.log.debug("Backfill request: %s", backfill_request)

        num_pages = backfill_request.num_pages
        self.log.debug(
            "Backfilling up to %d pages of history in %s through %s",
            num_pages,
            self.mxid,
            source.mxid,
        )

        try:
            if first_message := await DBMessage.get_first_in_chat(self.fbid, self.fb_receiver):
                self.log.debug("There is a first message in the chat, fetching messages before it")
                resp = await source.client.fetch_messages(self.fbid, first_message.timestamp - 1)
                messages = resp.nodes
            else:
                self.log.debug(
                    "There is no first message in the chat, starting with the most recent messages"
                )
                resp = await source.client.fetch_messages(self.fbid, int(time.time() * 1000))
                messages = resp.nodes
        except RateLimitExceeded:
            backoff = self.config.get("bridge.backfill.backoff.message_history", 300)
            self.log.warning(
                f"Backfilling failed due to rate limit. Waiting for {backoff} seconds before "
                "resuming."
            )
            await asyncio.sleep(backoff)
            raise

        if len(messages) == 0:
            self.log.debug("No messages to backfill.")
            return None

        last_message_timestamp = messages[-1].timestamp

        pages_to_backfill = backfill_request.num_pages
        if backfill_request.max_total_pages > -1:
            pages_to_backfill = min(pages_to_backfill, backfill_request.max_total_pages)

        backfill_more = True
        pages_backfilled = 0
        for i in range(pages_to_backfill):
            (
                num_bridged,
                oldest_bridged_msg_ts,
                base_insertion_event_id,
            ) = await self.backfill_message_page(source, messages)
            pages_backfilled += 1

            if base_insertion_event_id:
                self.historical_base_insertion_event_id = base_insertion_event_id
                await self.save()

            # If nothing was bridged, then we want to check and see if there are messages before
            # this page, or if the only thing left in the chat is unbridgable messages.
            if num_bridged == 0 or i < pages_to_backfill - 1:
                # Sleep before fetching another page of messages.
                await asyncio.sleep(backfill_request.page_delay)

                # Fetch more messages
                try:
                    resp = await source.client.fetch_messages(self.fbid, oldest_bridged_msg_ts - 1)
                except RateLimitExceeded:
                    backoff = self.config.get("bridge.backfill.backoff.message_history", 300)
                    self.log.warning(
                        f"Backfilling failed due to rate limit. Waiting for {backoff} seconds "
                        "before resuming."
                    )
                    await asyncio.sleep(backoff)

                    # If we hit the rate limit, then we will want to give up for now, but enqueue
                    # additional backfill to do later.
                    break

                if not resp.nodes:
                    # There were no more messages, we are at the beginning of history, so just
                    # break.
                    backfill_more = False
                    break
                messages = resp.nodes

        if backfill_request.max_total_pages == -1:
            new_max_total_pages = -1
        else:
            new_max_total_pages = backfill_request.max_total_pages - pages_backfilled
            if new_max_total_pages <= 0:
                backfill_more = False

        if backfill_more:
            self.log.debug("Enqueueing more backfill")
            await Backfill.new(
                source.mxid,
                # Always enqueue subsequent backfills at the lowest priority
                2,
                self.fbid,
                self.fb_receiver,
                backfill_request.num_pages,
                backfill_request.page_delay,
                backfill_request.post_batch_delay,
                new_max_total_pages,
            ).insert()
        else:
            self.log.debug("No more messages to backfill")

        return last_message_timestamp

    async def backfill_message_page(
        self,
        source: u.User,
        message_page: list[graphql.Message],
        forward: bool = False,
        last_message: DBMessage | None = None,
        mark_read: bool = False,
    ) -> tuple[int, int, EventID | None]:
        """
        Backfills a page of messages to Matrix. The messages should be in order from oldest to
        newest.

        Returns: a tuple containing the number of messages that were actually bridged, the
            timestamp of the oldest bridged message and the base insertion event ID if it exists.
        """
        assert source.client
        if len(message_page) == 0:
            return 0, 0, None

        if forward:
            assert (last_message and last_message.mxid) or self.first_event_id
            prev_event_id = last_message.mxid if last_message else self.first_event_id
        else:
            assert self.config["bridge.backfill.msc2716"]
            assert self.first_event_id
            prev_event_id = self.first_event_id

        assert self.mxid

        oldest_message_in_page = message_page[0]
        oldest_msg_timestamp = oldest_message_in_page.timestamp

        batch_messages: list[BatchSendEvent] = []
        state_events_at_start: list[BatchSendStateEvent] = []

        added_members = set()
        current_members = await self.main_intent.state_store.get_members(
            self.mxid, memberships=(Membership.JOIN,)
        )

        def add_member(puppet: p.Puppet, mxid: UserID):
            assert self.mxid
            if mxid in added_members:
                return
            if (
                self.bridge.homeserver_software.is_hungry
                or not self.config["bridge.backfill.msc2716"]
            ):
                # Hungryserv doesn't expect or check state events at start.
                added_members.add(mxid)
                return

            content_args = {"avatar_url": puppet.photo_mxc, "displayname": puppet.name}
            state_events_at_start.extend(
                [
                    BatchSendStateEvent(
                        content=MemberStateEventContent(Membership.INVITE, **content_args),
                        type=EventType.ROOM_MEMBER,
                        sender=self.main_intent.mxid,
                        state_key=mxid,
                        timestamp=oldest_msg_timestamp,
                    ),
                    BatchSendStateEvent(
                        content=MemberStateEventContent(Membership.JOIN, **content_args),
                        type=EventType.ROOM_MEMBER,
                        sender=mxid,
                        state_key=mxid,
                        timestamp=oldest_msg_timestamp,
                    ),
                ]
            )
            added_members.add(mxid)

        async def intent_for(user_id: str | int) -> tuple[p.Puppet, IntentAPI]:
            puppet: p.Puppet = await p.Puppet.get_by_fbid(user_id)
            if puppet:
                intent = puppet.intent_for(self)
            else:
                intent = self.main_intent
            if puppet.is_real_user and not self._can_double_puppet_backfill(intent.mxid):
                intent = puppet.default_mxid_intent
            return puppet, intent

        message_infos: list[tuple[graphql.Message, int]] = []
        intents: list[IntentAPI] = []
        last_message_timestamp = 0

        for message in message_page:
            last_message_timestamp = max(last_message_timestamp, message.timestamp)

            puppet, intent = await intent_for(message.message_sender.id)
            if not puppet.name:
                await puppet.update_info(source)

            # Convert the message
            converted = await self.convert_facebook_message(
                source,
                intent,
                message,
                deterministic_reply_id=self.bridge.homeserver_software.is_hungry,
            )
            if not converted:
                self.log.debug("Skipping unsupported message in backfill")
                continue

            if intent.mxid not in current_members:
                add_member(puppet, intent.mxid)

            d_event_id = None
            for index, (event_type, content) in enumerate(converted):
                if self.encrypted and self.matrix.e2ee:
                    event_type, content = await self.matrix.e2ee.encrypt(
                        self.mxid, event_type, content
                    )
                if intent.api.is_real_user and intent.api.bridge_name is not None:
                    content[DOUBLE_PUPPET_SOURCE_KEY] = intent.api.bridge_name

                if self.bridge.homeserver_software.is_hungry:
                    d_event_id = self._deterministic_event_id(message.message_id, index)

                message_infos.append((message, index))
                batch_messages.append(
                    BatchSendEvent(
                        content=content,
                        type=event_type,
                        sender=intent.mxid,
                        timestamp=message.timestamp,
                        event_id=d_event_id,
                    )
                )
                intents.append(intent)

            if self.bridge.homeserver_software.is_hungry and message.message_reactions:
                for reaction in message.message_reactions:
                    puppet, intent = await intent_for(reaction.user.id)

                    reaction_event = ReactionEventContent()
                    reaction_event.relates_to = RelatesTo(
                        rel_type=RelationType.ANNOTATION,
                        event_id=d_event_id,
                        key=reaction.reaction,
                    )
                    if intent.api.is_real_user and intent.api.bridge_name is not None:
                        reaction_event[DOUBLE_PUPPET_SOURCE_KEY] = intent.api.bridge_name

                    message_infos.append((reaction, 0))
                    batch_messages.append(
                        BatchSendEvent(
                            content=reaction_event,
                            type=EventType.REACTION,
                            sender=intent.mxid,
                            timestamp=message.timestamp,
                        )
                    )

        if not batch_messages:
            # Still return the oldest message's timestamp, since none of the messages were
            # bridgeable, we want to skip further back in history to find some that are bridgable.
            return 0, oldest_msg_timestamp, None

        if (
            not self.bridge.homeserver_software.is_hungry
            and self.config["bridge.backfill.msc2716"]
            and (forward or self.next_batch_id is None)
        ):
            self.log.debug("Sending dummy event to avoid forward extremity errors")
            await self.main_intent.send_message_event(
                self.mxid, EventType("fi.mau.dummy.pre_backfill", EventType.Class.MESSAGE), {}
            )

        self.log.info(
            "Sending %d %s messages to %s with batch ID %s and previous event ID %s",
            len(batch_messages),
            "new" if forward else "historical",
            self.mxid,
            self.next_batch_id,
            prev_event_id,
        )
        base_insertion_event_id = None
        if self.config["bridge.backfill.msc2716"]:
            batch_send_resp = await self.main_intent.batch_send(
                self.mxid,
                prev_event_id,
                batch_id=self.next_batch_id,
                events=batch_messages,
                state_events_at_start=state_events_at_start,
                beeper_new_messages=forward,
                beeper_mark_read_by=source.mxid if mark_read else None,
            )
            base_insertion_event_id = batch_send_resp.base_insertion_event_id
            event_ids = batch_send_resp.event_ids
        else:
            batch_send_resp = None
            event_ids = [
                await intent.send_message_event(
                    self.mxid, evt.type, evt.content, timestamp=evt.timestamp
                )
                for evt, intent in zip(batch_messages, intents)
            ]
        await self._finish_batch(event_ids, message_infos)
        if not forward:
            assert batch_send_resp
            self.log.debug("Got next batch ID %s for %s", batch_send_resp.next_batch_id, self.mxid)
            self.next_batch_id = batch_send_resp.next_batch_id
        await self.save()

        return (
            len(event_ids),
            oldest_msg_timestamp,
            base_insertion_event_id,
        )

    def _can_double_puppet_backfill(self, custom_mxid: UserID) -> bool:
        return self.config["bridge.backfill.double_puppet_backfill"] and (
            # Hungryserv can batch send any users
            self.bridge.homeserver_software.is_hungry
            # Non-MSC2716 backfill can use any double puppet
            or not self.config["bridge.backfill.msc2716"]
            # Local users can be double puppeted even with MSC2716
            or (custom_mxid[custom_mxid.index(":") + 1 :] == self.config["homeserver.domain"])
        )

    async def _finish_batch(
        self,
        event_ids: list[EventID],
        message_infos: list[tuple[graphql.Message | graphql.Reaction, int]],
    ):
        # We have to do this slightly annoying processing of the event IDs and message infos so
        # that we only map the last event ID to the message.
        # When inline captions are enabled, this will have no effect since index will always be 0
        # since there's only ever one event per message.
        current_message = None
        messages = []
        reactions = []
        for event_id, (message_or_reaction, index) in zip(event_ids, message_infos):
            if isinstance(message_or_reaction, graphql.Message):
                message = message_or_reaction
                if index == 0 and current_message:
                    # This means that all of the events for the previous message have been processed,
                    # and the current_message is the most recent event for that message.
                    messages.append(current_message)

                current_message = DBMessage(
                    mxid=event_id,
                    mx_room=self.mxid,
                    index=index,
                    timestamp=message.timestamp,
                    fbid=message.message_id,
                    fb_chat=self.fbid,
                    fb_receiver=self.fb_receiver,
                    fb_sender=int(message.message_sender.id),
                    fb_txn_id=int(message.offline_threading_id),
                )
            else:
                assert current_message
                reaction = message_or_reaction
                reactions.append(
                    DBReaction(
                        mxid=event_id,
                        mx_room=self.mxid,
                        fb_msgid=current_message.fbid,
                        fb_receiver=self.fb_receiver,
                        fb_sender=int(reaction.user.id),
                        reaction=reaction.reaction,
                        mx_timestamp=current_message.timestamp,
                    )
                )

        if current_message:
            messages.append(current_message)

        try:
            await DBMessage.bulk_insert(messages)
        except Exception:
            self.log.exception("Failed to store batch message IDs")

        try:
            for reaction in reactions:
                await reaction.insert()
        except Exception:
            self.log.exception("Failed to store backfilled reactions")

    async def send_post_backfill_dummy(
        self,
        last_message_timestamp: int,
        base_insertion_event_id: EventID | None = None,
    ):
        assert self.mxid

        if not base_insertion_event_id:
            base_insertion_event_id = self.historical_base_insertion_event_id

        if not base_insertion_event_id:
            self.log.debug(
                "No base insertion event ID in database or from batch send response. Not sending"
                " dummy event."
            )
            return

        event_id = await self.main_intent.send_message_event(
            self.mxid,
            event_type=HistorySyncMarkerMessage,
            content={
                "org.matrix.msc2716.marker.insertion": base_insertion_event_id,
                "m.marker.insertion": base_insertion_event_id,
            },
        )
        await DBMessage(
            mxid=event_id,
            mx_room=self.mxid,
            index=0,
            timestamp=last_message_timestamp + 1,
            fbid=None,
            fb_chat=self.fbid,
            fb_receiver=self.fb_receiver,
            fb_sender=0,
            fb_txn_id=None,
        ).insert()

    # endregion
    # region Matrix event handling

    def require_send_lock(self, user_id: int) -> asyncio.Lock:
        try:
            lock = self._send_locks[user_id]
        except KeyError:
            lock = asyncio.Lock()
            self._send_locks[user_id] = lock
        return lock

    def optional_send_lock(self, user_id: int) -> asyncio.Lock | FakeLock:
        try:
            return self._send_locks[user_id]
        except KeyError:
            pass
        return self._noop_lock

    async def _send_delivery_receipt(self, event_id: EventID) -> None:
        if event_id and self.config["bridge.delivery_receipts"]:
            try:
                await self.az.intent.mark_read(self.mxid, event_id)
            except Exception:
                self.log.exception(f"Failed to send delivery receipt for {event_id}")

    async def _send_bridge_success(
        self,
        sender: u.User,
        event_id: EventID,
        event_type: EventType,
        msgtype: MessageType | None = None,
    ) -> None:
        sender.send_remote_checkpoint(
            status=MessageSendCheckpointStatus.SUCCESS,
            event_id=event_id,
            room_id=self.mxid,
            event_type=event_type,
            message_type=msgtype,
        )
        background_task.create(self._send_message_status(event_id, err=None))
        await self._send_delivery_receipt(event_id)

    async def _send_bridge_error(
        self,
        sender: u.User,
        err: Exception,
        event_id: EventID,
        event_type: EventType,
        message_type: MessageType | None = None,
    ) -> None:
        sender.send_remote_checkpoint(
            self._status_from_exception(err),
            event_id,
            self.mxid,
            event_type,
            message_type=message_type,
            error=err,
        )

        send_notice = not isinstance(err, NotImplementedError)
        if self.config["bridge.delivery_error_reports"] and send_notice:
            event_type_str = {
                EventType.REACTION: "reaction",
                EventType.ROOM_REDACTION: "redaction",
            }.get(event_type, "message")
            await self._send_message(
                self.main_intent,
                TextMessageEventContent(
                    msgtype=MessageType.NOTICE,
                    body=f"\u26a0 Your {event_type_str} may not have been bridged: {str(err)}",
                ),
            )
        background_task.create(self._send_message_status(event_id, err))

    async def _send_message_status(self, event_id: EventID, err: Exception | None) -> None:
        if not self.config["bridge.message_status_events"]:
            return
        intent = self.az.intent if self.encrypted else self.main_intent
        status = BeeperMessageStatusEventContent(
            network=self.bridge_info_state_key,
            relates_to=RelatesTo(
                rel_type=RelationType.REFERENCE,
                event_id=event_id,
            ),
        )
        if err:
            status.status = MessageStatus.RETRIABLE
            status.reason = MessageStatusReason.GENERIC_ERROR
            status.error = str(err)
            if isinstance(err, NotImplementedError):
                status.status = MessageStatus.FAIL
                status.reason = MessageStatusReason.UNSUPPORTED
        else:
            status.status = MessageStatus.SUCCESS

        await intent.send_message_event(
            room_id=self.mxid,
            event_type=EventType.BEEPER_MESSAGE_STATUS,
            content=status,
        )

    @staticmethod
    def _status_from_exception(e: Exception) -> MessageSendCheckpointStatus:
        if isinstance(e, NotImplementedError):
            return MessageSendCheckpointStatus.UNSUPPORTED
        return MessageSendCheckpointStatus.PERM_FAILURE

    async def handle_matrix_message(
        self, sender: u.User, message: MessageEventContent, event_id: EventID
    ) -> None:
        try:
            await self._handle_matrix_message(sender, message, event_id)
        except Exception as e:
            self.log.exception(f"Failed to handle Matrix event {event_id}")
            await self._send_bridge_error(
                sender, e, event_id, EventType.ROOM_MESSAGE, message.msgtype
            )
        else:
            await self._send_bridge_success(
                sender, event_id, EventType.ROOM_MESSAGE, message.msgtype
            )

    async def _handle_matrix_message(
        self, orig_sender: u.User, message: MessageEventContent, event_id: EventID
    ) -> None:
        if message.get_edit():
            raise NotImplementedError("Edits are not supported by the Facebook bridge.")
        sender, is_relay = await self.get_relay_sender(orig_sender, f"message {event_id}")
        if not sender:
            raise Exception("not logged in")
        elif not sender.mqtt:
            raise Exception("not connected to MQTT")
        elif is_relay:
            await self.apply_relay_message_format(orig_sender, message)
        if message.msgtype == MessageType.TEXT or message.msgtype == MessageType.NOTICE:
            await self._handle_matrix_text(event_id, sender, message)
        elif message.msgtype.is_media:
            await self._handle_matrix_media(event_id, sender, message, is_relay)
        else:
            raise NotImplementedError(f"Unsupported message type {message.msgtype}")

    async def _make_dbm(self, sender: u.User, event_id: EventID) -> DBMessage:
        oti = sender.mqtt.generate_offline_threading_id()
        dbm = DBMessage(
            mxid=event_id,
            mx_room=self.mxid,
            fb_txn_id=oti,
            index=0,
            fb_chat=self.fbid,
            fb_receiver=self.fb_receiver,
            fb_sender=sender.fbid,
            timestamp=int(time.time() * 1000),
            fbid=None,
        )
        self._oti_dedup[oti] = dbm
        await dbm.insert()
        return dbm

    async def _handle_matrix_text(
        self, event_id: EventID, sender: u.User, message: TextMessageEventContent
    ) -> None:
        if (
            message.msgtype == MessageType.NOTICE
            and not self.config["bridge.bridge_matrix_notices"]
        ):
            return
        converted = await matrix_to_facebook(message, self.mxid, self.log)
        dbm = await self._make_dbm(sender, event_id)

        resp = await sender.mqtt.send_message(
            self.fbid,
            self.fb_type != ThreadType.USER,
            message=converted.text,
            mentions=converted.mentions,
            reply_to=converted.reply_to,
            offline_threading_id=dbm.fb_txn_id,
        )
        if not resp.success and resp.error_message:
            self.log.debug(f"Error handling Matrix message {event_id}: {resp.error_message}")
            raise Exception(resp.error_message)
        else:
            self.log.debug(f"Handled Matrix message {event_id} -> OTI: {dbm.fb_txn_id}")

    async def _handle_matrix_media(
        self, event_id: EventID, sender: u.User, message: MediaMessageEventContent, is_relay: bool
    ) -> None:
        if message.file and decrypt_attachment:
            data = await self.main_intent.download_media(message.file.url)
            data = decrypt_attachment(
                data, message.file.key.key, message.file.hashes.get("sha256"), message.file.iv
            )
        elif message.url:
            data = await self.main_intent.download_media(message.url)
        else:
            raise NotImplementedError("No file or URL specified")
        mime = message.info.mimetype or magic.mimetype(data)
        dbm = await self._make_dbm(sender, event_id)
        reply_to = None
        reply_to_mxid = message.get_reply_to()
        if reply_to_mxid:
            reply_to_msg = await DBMessage.get_by_mxid(reply_to_mxid, self.mxid)
            if reply_to_msg:
                reply_to = reply_to_msg.fbid
            else:
                self.log.warning(
                    f"Couldn't find reply target {reply_to_mxid}"
                    " to bridge media message reply metadata to Facebook"
                )
        filename = message.body
        if is_relay:
            caption = (await matrix_to_facebook(message, self.mxid, self.log)).text
        else:
            caption = None
        if message.msgtype == MessageType.AUDIO:
            if not mime.startswith("audio/mp"):
                data = await ffmpeg.convert_bytes(
                    data,
                    output_extension=".m4a",
                    output_args=("-c:a", "aac"),
                    input_mime=mime,
                )
                mime = "audio/mpeg"
                filename = "audio.m4a"
            duration = message.info.duration
        else:
            duration = None
        # await sender.mqtt.opened_thread(self.fbid)
        resp = await sender.client.send_media(
            data,
            filename,
            mime,
            caption=caption,
            offline_threading_id=dbm.fb_txn_id,
            reply_to=reply_to,
            chat_id=self.fbid,
            is_group=self.fb_type != ThreadType.USER,
            duration=duration,
        )
        if not resp.media_id and resp.debug_info:
            self.log.debug(
                f"Error uploading media for Matrix message {event_id}: {resp.debug_info.message}"
            )
            raise Exception(f"Media upload error: {resp.debug_info.message}")

        try:
            self._oti_dedup.pop(dbm.fb_txn_id)
        except KeyError:
            self.log.trace(f"Message ID for OTI {dbm.fb_txn_id} seems to have been found already")
        else:
            dbm.fbid = resp.message_id
            # TODO can we find the timestamp?
            await dbm.update()
        self.log.debug(f"Handled Matrix message {event_id} -> {resp.message_id} / {dbm.fb_txn_id}")

    async def handle_matrix_redaction(
        self, sender: u.User, event_id: EventID, redaction_event_id: EventID
    ) -> None:
        try:
            await self._handle_matrix_redaction(sender, event_id)
        except Exception as e:
            self.log.error(
                f"Failed to handle Matrix redaction {redaction_event_id}: {e}",
                exc_info=not isinstance(e, NotImplementedError),
            )
            await self._send_bridge_error(sender, e, redaction_event_id, EventType.ROOM_REDACTION)
        else:
            await self._send_bridge_success(sender, redaction_event_id, EventType.ROOM_REDACTION)

    async def _handle_matrix_redaction(self, sender: u.User, event_id: EventID) -> None:
        sender, _ = await self.get_relay_sender(sender, f"redaction {event_id}")
        if not sender:
            raise Exception("not logged in")
        message = await DBMessage.get_by_mxid(event_id, self.mxid)
        if message:
            if not message.fbid:
                track(sender, "$unknown_message_fbid")
                raise NotImplementedError("Tried to redact message whose fbid is unknown")
            try:
                await message.delete()
                await sender.client.unsend(message.fbid)
            except Exception as e:
                self.log.exception(f"Failed to unsend {message.fbid}")
                raise
            return

        reaction = await DBReaction.get_by_mxid(event_id, self.mxid)
        if reaction:
            try:
                await reaction.delete()
                await sender.client.react(reaction.fb_msgid, None)
            except Exception as e:
                self.log.exception(f"Failed to remove reaction to {reaction.fb_msgid}")
                raise
            return

        raise NotImplementedError("redaction target not found")

    async def handle_matrix_reaction(
        self,
        sender: u.User,
        event_id: EventID,
        reacting_to: EventID,
        reaction: str,
        timestamp: int,
    ) -> None:
        try:
            await self._handle_matrix_reaction(sender, event_id, reacting_to, reaction, timestamp)
        except Exception as e:
            self.log.error(
                f"Failed to handle Matrix reaction {event_id}: {e}",
                exc_info=not isinstance(e, NotImplementedError),
            )
            await self._send_bridge_error(sender, e, event_id, EventType.REACTION)
        else:
            await self._send_bridge_success(sender, event_id, EventType.REACTION)

    async def _handle_matrix_reaction(
        self,
        sender: u.User,
        event_id: EventID,
        reacting_to: EventID,
        reaction: str,
        timestamp: int,
    ) -> None:
        sender, is_relay = await self.get_relay_sender(sender, f"reaction {event_id}")
        if not sender or is_relay:
            raise NotImplementedError("not logged in")
        # Facebook doesn't use variation selectors, Matrix does
        reaction = variation_selector.remove(reaction)

        async with self.require_send_lock(sender.fbid):
            message = await DBMessage.get_by_mxid(reacting_to, self.mxid)
            if not message:
                raise NotImplementedError("reaction target message not found")
            elif not message.fbid:
                track(sender, "$unknown_message_fbid")
                raise NotImplementedError("facebook ID of target message is unknown")

            existing = await DBReaction.get_by_fbid(message.fbid, self.fb_receiver, sender.fbid)
            if existing and existing.reaction == reaction:
                return

            await sender.client.react(message.fbid, reaction)
            await self._upsert_reaction(
                existing, self.main_intent, event_id, message, sender, reaction, timestamp
            )

    async def handle_matrix_leave(self, user: u.User) -> None:
        if self.is_direct:
            self.log.info(f"{user.mxid} left private chat portal with {self.fbid}")
            if user.fbid == self.fb_receiver:
                self.log.info(
                    f"{user.mxid} was the recipient of this portal. Cleaning up and deleting..."
                )
                await self.cleanup_and_delete()
        else:
            self.log.debug(f"{user.mxid} left portal to {self.fbid}")

    async def _set_typing(self, users: set[UserID], typing: bool) -> None:
        for mxid in users:
            user: u.User = await u.User.get_by_mxid(mxid, create=False)
            if user and user.mqtt:
                await user.mqtt.set_typing(self.fbid, typing)

    async def handle_matrix_typing(self, users: set[UserID]) -> None:
        await asyncio.gather(
            self._set_typing(users - self._typing, typing=True),
            self._set_typing(self._typing - users, typing=False),
        )
        self._typing = users

    # endregion
    # region Facebook event handling

    async def _bridge_own_message_pm(
        self, source: u.User, sender: p.Puppet, mid: str, invite: bool = True
    ) -> bool:
        if self.is_direct and sender.fbid == source.fbid and not sender.is_real_user:
            if self.invite_own_puppet_to_pm and invite:
                await self.main_intent.invite_user(self.mxid, sender.mxid)
            elif (
                await self.az.state_store.get_membership(self.mxid, sender.mxid) != Membership.JOIN
            ):
                self.log.warning(
                    f"Ignoring own {mid} in private chat because own puppet is not in room."
                )
                return False
        return True

    async def _add_facebook_reply(
        self,
        content: MessageEventContent,
        reply_to: graphql.MinimalMessage | mqtt.Message,
        deterministic_id: bool = False,
    ) -> None:
        if isinstance(reply_to, graphql.MinimalMessage):
            log_msg_id = reply_to.message_id
            message = await DBMessage.get_by_fbid(reply_to.message_id, self.fb_receiver)
        elif isinstance(reply_to, mqtt.Message):
            meta = reply_to.metadata
            log_msg_id = f"{meta.id} / {meta.offline_threading_id}"
            message = await DBMessage.get_by_fbid_or_oti(
                meta.id, meta.offline_threading_id, self.fb_receiver, meta.sender
            )
            if message and not message.fbid:
                self.log.debug(
                    f"Got message ID {meta.id} for offline threading ID "
                    f"{message.fb_txn_id} / {message.mxid} (in database) from reply"
                )
                message.fbid = meta.id
                message.timestamp = meta.timestamp
                await message.update()
        else:
            return

        if not message:
            if deterministic_id and isinstance(reply_to, graphql.MinimalMessage):
                content.set_reply(self._deterministic_event_id(reply_to.message_id, 0))
            else:
                self.log.warning(
                    f"Couldn't find reply target {log_msg_id} to bridge reply metadata to Matrix"
                )
            return

        content.set_reply(message.mxid)
        if not isinstance(content, TextMessageEventContent) or self.disable_reply_fallbacks:
            return

        try:
            evt = await self.main_intent.get_event(message.mx_room, message.mxid)
        except (MNotFound, MForbidden):
            evt = None
        except Exception:
            self.log.warning("Failed to fetch event for generating reply fallback", exc_info=True)
            return
        if not evt:
            return

        if evt.type == EventType.ROOM_ENCRYPTED:
            try:
                evt = await self.matrix.e2ee.decrypt(evt, wait_session_timeout=0)
            except DecryptionError:
                return
            except Exception:
                self.log.warning(
                    "Failed to decrypt event for generating reply fallback", exc_info=True
                )
                return

        if isinstance(evt.content, TextMessageEventContent):
            evt.content.trim_reply_fallback()

        content.set_reply(evt)

    async def handle_facebook_message(
        self,
        source: u.User,
        sender: p.Puppet,
        message: graphql.Message | mqtt.Message,
        reply_to: mqtt.Message | None = None,
    ) -> None:
        try:
            await self._handle_facebook_message(source, sender, message, reply_to)
        except Exception:
            self.log.exception(
                "Error handling Facebook message %s",
                message.message_id
                if isinstance(message, graphql.Message)
                else message.metadata.id,
            )

    async def _handle_facebook_message(
        self,
        source: u.User,
        sender: p.Puppet,
        message: graphql.Message | mqtt.Message,
        reply_to: mqtt.Message | None = None,
    ) -> None:
        if isinstance(message, graphql.Message):
            self.log.trace("Facebook GraphQL event content: %s", message)
            msg_id = message.message_id
            oti = int(message.offline_threading_id)
            timestamp = message.timestamp

            def backfill_reactions(dbm: DBMessage | None):
                background_task.create(
                    self._try_handle_graphql_reactions(
                        source, dbm or msg_id, message.message_reactions
                    )
                )

        elif isinstance(message, mqtt.Message):
            self.log.trace("Facebook MQTT event content: %s", message)
            msg_id = message.metadata.id
            oti = message.metadata.offline_threading_id
            timestamp = message.metadata.timestamp

            def backfill_reactions(_):
                pass

        else:
            raise ValueError(f"Invalid message class {type(message).__name__}")

        # Check in-memory queues for duplicates
        if oti in self._oti_dedup:
            dbm = self._oti_dedup.pop(oti)
            self._dedup.appendleft(msg_id)
            self.log.debug(
                f"Got message ID {msg_id} for offline threading ID {oti} / {dbm.mxid}"
                " (in dedup queue)"
            )
            dbm.fbid = msg_id
            dbm.timestamp = timestamp
            await dbm.update()
            backfill_reactions(dbm)
            return
        elif msg_id in self._dedup:
            self.log.trace("Not handling message %s, found ID in dedup queue", msg_id)
            backfill_reactions(None)
            return

        self._dedup.appendleft(msg_id)

        # Check database for duplicates
        dbm = await DBMessage.get_by_fbid_or_oti(msg_id, oti, self.fb_receiver, sender.fbid)
        if dbm:
            if not dbm.fbid:
                self.log.debug(
                    f"Got message ID {msg_id} for offline threading ID {dbm.fb_txn_id} "
                    f"/ {dbm.mxid} (in database)"
                )
                dbm.fbid = msg_id
                dbm.timestamp = timestamp
                await dbm.update()
            else:
                self.log.debug(f"Not handling message {msg_id}, found duplicate in database")
            backfill_reactions(dbm)
            return

        self.log.debug(f"Handling Facebook event {msg_id} (/{oti})")
        if not self.mxid:
            mxid = await self.create_matrix_room(source)
            if not mxid:
                # Failed to create
                return

            if self.config["bridge.backfill.enable"]:
                if self.config["bridge.backfill.msc2716"]:
                    await self.enqueue_immediate_backfill(source, 0)
                # TODO backfill immediate page without MSC2716
        if not await self._bridge_own_message_pm(source, sender, f"message {msg_id}"):
            return
        intent = sender.intent_for(self)
        event_ids = []
        for event_type, content in await self.convert_facebook_message(
            source, intent, message, reply_to
        ):
            assert isinstance(message, (graphql.Message, mqtt.Message))
            timestamp = (
                message.timestamp
                if isinstance(message, graphql.Message)
                else message.metadata.timestamp
            )
            event_ids.append(
                await self._send_message(
                    intent, content, event_type=event_type, timestamp=timestamp
                )
            )
        event_ids = [event_id for event_id in event_ids if event_id]
        if not event_ids:
            self.log.warning(f"Unhandled Messenger message {msg_id}")
            return
        self.log.debug(f"Handled Messenger message {msg_id} -> {event_ids}")
        created_msgs = await DBMessage.bulk_create_parts(
            fbid=msg_id,
            oti=oti,
            fb_chat=self.fbid,
            fb_sender=sender.fbid,
            fb_receiver=self.fb_receiver,
            mx_room=self.mxid,
            timestamp=timestamp,
            event_ids=event_ids,
        )
        await self._send_delivery_receipt(event_ids[-1])
        if isinstance(message, graphql.Message) and message.message_reactions:
            await self._handle_graphql_reactions(
                source, created_msgs[0], message.message_reactions, timestamp
            )

    def _deterministic_event_id(self, message_id: int | str, index: int) -> EventID:
        hash_content = f"{self.mxid}/facebook/{message_id}/{index}"
        hashed = hashlib.sha256(hash_content.encode("utf-8")).digest()
        b64hash = base64.urlsafe_b64encode(hashed).decode("utf-8").rstrip("=")
        return EventID(f"${b64hash}:facebook.com")

    async def convert_facebook_message(
        self,
        source: u.User,
        intent: IntentAPI,
        message: graphql.Message | mqtt.Message,
        reply_to: mqtt.Message | None = None,
        deterministic_reply_id: bool = False,
    ) -> list[ConvertedMessage]:
        converted: list[ConvertedMessage] = []

        try:
            if message.montage_reply_data and message.montage_reply_data.snippet:
                converted.append(await self._convert_facebook_story_reply(message))

            if isinstance(message, graphql.Message):
                converted.extend(
                    await self._convert_graphql_message(
                        source, intent, message, deterministic_reply_id=deterministic_reply_id
                    )
                )
            else:
                converted.extend(
                    await self._convert_mqtt_message(source, intent, message, reply_to)
                )
        except Exception:
            self.log.exception(
                "Error converting Facebook message %s",
                message.message_id
                if isinstance(message, graphql.Message)
                else message.metadata.id,
            )

        return converted

    async def _convert_facebook_story_reply(
        self, message: mqtt.Message | graphql.Message
    ) -> ConvertedMessage:
        assert message.montage_reply_data and message.montage_reply_data.snippet
        text = message.montage_reply_data.snippet
        if message.montage_reply_data.message_id and message.montage_reply_data.montage_thread_id:
            card_id_data = f"S:_ISC:{message.montage_reply_data.message_id}"
            story_url = (
                URL("https://www.facebook.com/stories")
                / message.montage_reply_data.montage_thread_id
                / base64.b64encode(card_id_data.encode("utf-8")).decode("utf-8")
            )
            text += f" ({story_url})"
        return EventType.ROOM_MESSAGE, TextMessageEventContent(
            msgtype=MessageType.NOTICE, body=text
        )

    async def _convert_mqtt_message(
        self,
        source: u.User,
        intent: IntentAPI,
        message: mqtt.Message,
        reply_to: mqtt.Message | None,
    ) -> list[ConvertedMessage]:
        converted: list[ConvertedMessage] = []
        if message.sticker:
            converted.append(
                await self._convert_facebook_sticker(source, intent, message.sticker, reply_to)
            )
        if len(message.attachments) > 0:
            attachment_contents = await asyncio.gather(
                *[
                    self._convert_facebook_attachment(
                        message.metadata.id,
                        source,
                        intent,
                        attachment,
                        reply_to,
                        message_text=message.text,
                    )
                    for attachment in message.attachments
                ]
            )
            converted += [c for c in attachment_contents if c]
        if message.text:
            converted.append(await self._convert_facebook_text(message, reply_to))
        return converted

    async def _convert_extensible_media(
        self,
        source: u.User,
        intent: IntentAPI,
        sa: graphql.StoryAttachment,
        message_text: str | None,
    ) -> MessageEventContent | None:
        if sa.target and sa.target.typename == graphql.AttachmentType.EXTERNAL_URL:
            url = str(sa.clean_url)
            if message_text is not None and url in message_text:
                # URL is present in message, don't repost
                return None
            escaped_url = escape(url)
            return TextMessageEventContent(
                msgtype=MessageType.TEXT,
                format=Format.HTML,
                body=str(sa.clean_url),
                formatted_body=f'<a href="{escaped_url}">{escaped_url}</a>',
            )
        elif (
            sa.media
            and sa.media.typename_str in ("Image", "Video")
            and (sa.media.playable_url or sa.media.image_natural)
        ):
            if sa.media.typename_str == "Video":
                msgtype = MessageType.VIDEO
                info = VideoInfo()
                url = sa.media.playable_url
            elif sa.media.typename_str == "Image":
                msgtype = MessageType.IMAGE
                url = sa.media.image_natural.uri
                info = ImageInfo(
                    width=sa.media.image_natural.width,
                    height=sa.media.image_natural.height,
                )
            else:
                raise RuntimeError("Unexpected typename_str in extensible media handler")
            try:
                mxc, additional_info, decryption_info = await self._reupload_fb_file(
                    url, source, intent, encrypt=self.encrypted, find_size=False
                )
            except ValueError as e:
                self.log.debug("Failed to reupload story attachment media", exc_info=True)
                return TextMessageEventContent(
                    msgtype=MessageType.NOTICE,
                    body=f"{e}\n{sa.url}",
                    external_url=sa.url,
                )
            info.size = additional_info.size
            info.mimetype = additional_info.mimetype
            title = sa.title or sa.media.typename_str
            filename = f"{title}{mimetypes.guess_extension(info.mimetype)}"
            content = MediaMessageEventContent(
                url=mxc,
                file=decryption_info,
                msgtype=msgtype,
                body=filename,
                info=info,
                external_url=sa.url,
            )
            # TODO only do this if captions are enabled in the config
            if sa.description and sa.description.text != "msngr.com":
                content["filename"] = content.body
                content.body = sa.description.text
                if sa.url:
                    content.body += f"\n\n{sa.url}"
                    content["format"] = str(Format.HTML)
                    content["formatted_body"] = (
                        f"<p>{escape(sa.description.text)}</p>"
                        f"<p><a href='{sa.url}'>Open external link</a></p>"
                    )
            return content
        elif sa.url or sa.title or (sa.description and sa.description.text) or sa.action_links:
            url = str(sa.clean_url) if sa.url else None
            if not url:
                url = str(sa.xma_tpl_url)
            if message_text and ((url and url in message_text) or sa.title in message_text):
                # URL is present in message, don't repost
                return None
            text_parts = []
            html_parts = []
            if sa.title:
                text_parts.append(f"**{sa.title}**")
                html_parts.append(f"<p><strong>{escape(sa.title)}</strong></p>")
            if sa.description and sa.description.text and sa.description.text != "msngr.com":
                text_parts.append(sa.description.text)
                html_parts.append(f"<p>{escape(sa.description.text)}</p>")
            if url:
                text_parts.append(url)
                html_parts.append(f"<p>{escape(url)}</p>")
            elif sa.action_links:
                urls = [item.url for item in sa.action_links if item.url]
                if len(urls) > 0:
                    sa.url = urls[0]
                    text_parts.append(" - ".join(urls))
                    html_action_links = [
                        f"""<a href="{item.url}">{item.title}</a>"""
                        for item in sa.action_links
                        if item.url
                    ]
                    html_parts.append(f"""<p>{" - ".join(html_action_links)}</p>""")
            return TextMessageEventContent(
                msgtype=MessageType.TEXT,
                body="\n\n".join(text_parts),
                format=Format.HTML,
                formatted_body="".join(html_parts),
                external_url=sa.url,
            )
        else:
            self.log.debug("Unhandled story attachment: %s", sa.serialize())
            return None

    async def _convert_mqtt_attachment(
        self,
        msg_id: str,
        source: u.User,
        intent: IntentAPI,
        attachment: mqtt.Attachment,
        message_text: str,
    ) -> MessageEventContent | None:
        filename = attachment.file_name
        if attachment.mime_type and filename is not None and "." not in filename:
            filename += mimetypes.guess_extension(attachment.mime_type)
        referer = "unknown"
        voice_message = False
        if attachment.extensible_media:
            sa = attachment.parse_extensible().story_attachment
            self.log.trace("Story attachment %s content: %s", attachment.media_id_str, sa)
            return await self._convert_extensible_media(
                source, intent, sa, message_text=message_text
            )
        elif attachment.video_info:
            msgtype = MessageType.VIDEO
            url = attachment.video_info.download_url
            info = VideoInfo(
                duration=attachment.video_info.duration_ms,
                width=attachment.video_info.original_width,
                height=attachment.video_info.original_height,
            )
        elif attachment.audio_info:
            msgtype = MessageType.AUDIO
            url = attachment.audio_info.url
            info = AudioInfo(duration=attachment.audio_info.duration_ms)
            voice_message = True
            attachment.mime_type = None
        elif attachment.image_info:
            referer = "messenger_thread_photo"
            msgtype = MessageType.IMAGE
            info = ImageInfo(
                width=attachment.image_info.original_width,
                height=attachment.image_info.original_height,
            )
            if attachment.image_info.animated_uri_map:
                url = list(attachment.image_info.animated_uri_map.values())[0]
                # Override the mime type or detect from file
                attachment.mime_type = {
                    "webp": "image/webp",
                    "gif": "image/gif",
                    "png": "image/png",
                }.get(attachment.image_info.animated_image_type, None)
            else:
                url = list(attachment.image_info.uri_map.values())[0]
            # TODO find out if we need to use get_image_url in some cases even with MQTT
            # url = await source.client.get_image_url(msg_id, attachment.media_id)
        elif attachment.media_id:
            # TODO what if it's not a file?
            msgtype = MessageType.FILE
            url = await source.client.get_file_url(self.fbid, msg_id, attachment.media_id)
            info = FileInfo()
        else:
            self.log.warning(f"Unsupported attachment in {msg_id}")
            return TextMessageEventContent(
                msgtype=MessageType.NOTICE, body="Unsupported attachment"
            )
        mxc, additional_info, decryption_info = await self._reupload_fb_file(
            url,
            source,
            intent,
            filename=filename,
            encrypt=self.encrypted,
            find_size=False,
            referer=referer,
            convert_audio=voice_message,
        )
        info.size = additional_info.size
        info.mimetype = attachment.mime_type or additional_info.mimetype
        content = MediaMessageEventContent(
            url=mxc, file=decryption_info, msgtype=msgtype, body=filename, info=info
        )
        if voice_message:
            content["org.matrix.msc1767.audio"] = {"duration": info.duration}
            content["org.matrix.msc3245.voice"] = {}
            content.body += ".ogg"
        return content

    async def _try_handle_graphql_reactions(
        self,
        source: u.User,
        msg: str | DBMessage,
        reactions: list[graphql.Reaction],
        timestamp: int | None = None,
    ) -> None:
        try:
            await self._handle_graphql_reactions(source, msg, reactions, timestamp)
        except Exception:
            msg_id = msg.fbid if isinstance(msg, DBMessage) else msg
            self.log.exception(f"Error backfilling reactions to {msg_id}")

    async def _handle_graphql_reactions(
        self,
        source: u.User,
        msg: str | DBMessage,
        reactions: list[graphql.Reaction],
        timestamp: int | None = None,
    ) -> None:
        if isinstance(msg, DBMessage):
            message_id = msg.fbid
            target_message = msg
        else:
            message_id = msg
            target_message = None
        bridged_reactions = await DBReaction.get_by_message_fbid(message_id, self.fb_receiver)
        latest_reactions: dict[int, graphql.Reaction] = {
            int(react.user.id): react for react in reactions
        }
        self.log.trace(
            f"Syncing reactions of {message_id} (database has {len(bridged_reactions)}, data "
            f"from GraphQL has {len(latest_reactions)})"
        )
        tasks: list[asyncio.Task] = []
        deduplicated_timestamp = (timestamp + 1) if timestamp else int(time.time() * 1000)
        for sender, reaction in latest_reactions.items():
            try:
                existing = bridged_reactions[sender]
            except KeyError:
                task = self.handle_facebook_reaction_add(
                    source,
                    sender,
                    message_id,
                    reaction.reaction,
                    target_message=target_message,
                    # Timestamp is only used for new reactions, because it's only important when
                    # backfilling messages (which obviously won't have already bridged reactions).
                    timestamp=deduplicated_timestamp,
                )
                deduplicated_timestamp += 1
                tasks.append(asyncio.create_task(task))
            else:
                if existing.reaction != reaction.reaction:
                    task = self.handle_facebook_reaction_add(
                        source,
                        sender,
                        message_id,
                        reaction.reaction,
                        existing=existing,
                        target_message=target_message,
                    )
                    tasks.append(asyncio.create_task(task))
        for sender, existing in bridged_reactions.items():
            if sender not in latest_reactions:
                task = self.handle_facebook_reaction_remove(source, sender, existing)
                tasks.append(asyncio.create_task(task))
        if len(tasks) > 0:
            await asyncio.gather(*tasks)
            self.log.debug(f"Updated {len(tasks)} reactions of {message_id}")

    async def _convert_graphql_message(
        self,
        source: u.User,
        intent: IntentAPI,
        message: graphql.Message,
        deterministic_reply_id: bool = False,
    ) -> list[ConvertedMessage]:
        reply_to_msg = message.replied_to_message.message if message.replied_to_message else None
        converted: list[ConvertedMessage] = []
        if message.sticker:
            converted.append(
                await self._convert_facebook_sticker(
                    source, intent, int(message.sticker.id), reply_to_msg
                )
            )

        if len(message.blob_attachments) > 0:
            attachment_contents = await asyncio.gather(
                *[
                    self._convert_facebook_attachment(
                        message.message_id, source, intent, attachment, reply_to_msg
                    )
                    for attachment in message.blob_attachments
                ]
            )
            converted += [c for c in attachment_contents if c]

        text = message.message.text if message.message else None
        if message.extensible_attachment:
            sa = message.extensible_attachment.story_attachment
            content = await self._convert_extensible_media(source, intent, sa, message_text=text)
            if content:
                converted.append((EventType.ROOM_MESSAGE, content))
        if text:
            converted.append(
                await self._convert_facebook_text(
                    message.message, reply_to_msg, deterministic_reply_id=deterministic_reply_id
                )
            )
        return converted

    async def _convert_facebook_text(
        self,
        message: graphql.MessageText | mqtt.Message,
        reply_to: graphql.MinimalMessage | mqtt.Message,
        deterministic_reply_id: bool = False,
    ) -> ConvertedMessage:
        content = await facebook_to_matrix(message)
        await self._add_facebook_reply(content, reply_to, deterministic_id=deterministic_reply_id)
        return EventType.ROOM_MESSAGE, content

    async def _convert_facebook_sticker(
        self,
        source: u.User,
        intent: IntentAPI,
        sticker_id: int,
        reply_to: graphql.MinimalMessage | mqtt.Message,
    ) -> ConvertedMessage:
        assert source.client
        resp = await source.client.fetch_stickers([sticker_id], sticker_labels_enabled=True)
        sticker = resp.nodes[0]
        url = (sticker.animated_image or sticker.thread_image).uri
        mxc, info, decryption_info = await self._reupload_fb_file(
            url, source, intent, encrypt=self.encrypted, find_size=True
        )
        content = MediaMessageEventContent(
            url=mxc,
            file=decryption_info,
            info=info,
            msgtype=MessageType.STICKER,
            body=sticker.label or "",
        )
        await self._add_facebook_reply(content, reply_to)
        return EventType.STICKER, content

    async def _convert_facebook_attachment(
        self,
        msg_id: str,
        source: u.User,
        intent: IntentAPI,
        attachment: graphql.Attachment | mqtt.Attachment,
        reply_to: graphql.MinimalMessage | mqtt.Message,
        message_text: str | None = None,
    ) -> ConvertedMessage | None:
        if isinstance(attachment, graphql.Attachment):
            content = await self._convert_graphql_attachment(msg_id, source, intent, attachment)
        elif isinstance(attachment, mqtt.Attachment):
            content = await self._convert_mqtt_attachment(
                msg_id, source, intent, attachment, message_text=message_text
            )
        else:
            raise ValueError(f"Invalid attachment type {type(attachment).__name__}")
        if not content:
            return None
        await self._add_facebook_reply(content, reply_to)
        return EventType.ROOM_MESSAGE, content

    async def _convert_graphql_attachment(
        self,
        msg_id: str,
        source: u.User,
        intent: IntentAPI,
        attachment: graphql.Attachment,
    ) -> MessageEventContent:
        filename = attachment.filename
        if attachment.mimetype and "." not in filename:
            filename += mimetypes.guess_extension(attachment.mimetype)
        referer = "unknown"
        if attachment.typename in (
            graphql.AttachmentType.IMAGE,
            graphql.AttachmentType.ANIMATED_IMAGE,
        ):
            msgtype = MessageType.IMAGE
            if attachment.typename == graphql.AttachmentType.IMAGE:
                info = ImageInfo(
                    width=attachment.original_dimensions.x,
                    height=attachment.original_dimensions.y,
                    mimetype=attachment.mimetype,
                )
                full_screen = attachment.image_full_screen
            else:
                info = ImageInfo(
                    width=attachment.animated_image_original_dimensions.x,
                    height=attachment.animated_image_original_dimensions.y,
                    mimetype=attachment.mimetype,
                )
                full_screen = attachment.animated_image_full_screen
            url = full_screen.uri
            if (info.width, info.height) > full_screen.dimensions:
                url = await source.client.get_image_url(msg_id, attachment.attachment_fbid) or url
            referer = "messenger_thread_photo"
        elif attachment.typename == graphql.AttachmentType.AUDIO:
            msgtype = MessageType.AUDIO
            info = AudioInfo(
                duration=attachment.playable_duration_in_ms,
                mimetype=attachment.mimetype,
            )
            url = attachment.playable_url
        elif attachment.typename == graphql.AttachmentType.VIDEO:
            msgtype = MessageType.VIDEO
            info = VideoInfo(
                duration=attachment.playable_duration_in_ms,
                mimetype=attachment.mimetype,
            )
            url = attachment.attachment_video_url
        elif attachment.typename == graphql.AttachmentType.FILE:
            msgtype = MessageType.FILE
            url = await source.client.get_file_url(self.fbid, msg_id, attachment.attachment_fbid)
            info = FileInfo(mimetype=attachment.mimetype)
        else:
            # TODO location attachments
            msg = f"Unsupported attachment type {attachment.typename}"
            self.log.warning(msg)
            return TextMessageEventContent(msgtype=MessageType.NOTICE, body=msg)
        mxc, additional_info, decryption_info = await self._reupload_fb_file(
            url,
            source,
            intent,
            filename=filename,
            encrypt=self.encrypted,
            find_size=False,
            referer=referer,
        )
        info.size = additional_info.size
        return MediaMessageEventContent(
            url=mxc, file=decryption_info, msgtype=msgtype, body=filename, info=info
        )

    async def _convert_facebook_location(
        self, source: u.User, intent: IntentAPI, location: graphql.StoryTarget
    ) -> LocationMessageEventContent | TextMessageEventContent:
        long, lat = location.coordinates.longitude, location.coordinates.latitude
        if not long or not lat:
            # if location.address or location.url:
            #     self.log.trace("Location message with no coordinates: %s", location)
            #     return TextMessageEventContent(msgtype=MessageType.TEXT,
            #                                    body=f"{location.address}\n{location.url}")
            # else:
            self.log.warning("Unsupported Facebook location message content: %s", location)
            return TextMessageEventContent(
                msgtype=MessageType.NOTICE,
                body="Location message with unsupported content",
            )
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        geo = f"{round(lat, 6)},{round(long, 6)}"

        text = f"{round(abs(lat), 4)}° {lat_char}, {round(abs(long), 4)}° {long_char}"
        url = f"https://maps.google.com/?q={geo}"

        content = LocationMessageEventContent(
            body=f"Location: {text}\n{url}",
            geo_uri=f"geo:{lat},{long}",
            msgtype=MessageType.LOCATION,
        )
        # Some clients support formatted body in m.location, so add that as well.
        content["format"] = str(Format.HTML)
        content["formatted_body"] = f"<p>Location: <a href='{url}'>{text}</a></p"
        # TODO find out if locations still have addresses
        # if location.address:
        #     content.body = f"{location.address}\n{content.body}"
        #     content["formatted_body"] = f"<p>{location.address}</p>{content['formatted_body']}"
        return content

    async def handle_facebook_unsend(
        self, sender: p.Puppet, message_id: str, timestamp: int
    ) -> None:
        if not self.mxid:
            return
        for message in await DBMessage.get_all_by_fbid(message_id, self.fb_receiver):
            try:
                await sender.intent_for(self).redact(
                    message.mx_room, message.mxid, timestamp=timestamp
                )
            except MForbidden:
                await self.main_intent.redact(message.mx_room, message.mxid, timestamp=timestamp)
            await message.delete()

    async def handle_facebook_seen(self, source: u.User, sender: p.Puppet, timestamp: int) -> None:
        if not self.mxid:
            return
        msg = await DBMessage.get_closest_before(self.fbid, self.fb_receiver, timestamp)
        if not msg:
            return
        if not await self._bridge_own_message_pm(source, sender, "read receipt", invite=False):
            return
        # TODO can we set a timestamp when the read receipt happened?
        await sender.intent_for(self).mark_read(msg.mx_room, msg.mxid)
        self.log.debug(
            f"Handled Messenger read receipt from {sender.fbid} up to {timestamp}/{msg.mxid}"
        )

    async def handle_facebook_photo(
        self,
        source: u.User,
        sender: p.Puppet,
        new_photo: mqtt.Attachment,
        message_id: str,
        timestamp: int,
    ) -> None:
        if not self.mxid or self.is_direct or message_id in self._dedup:
            return
        self._dedup.appendleft(message_id)
        photo_url = await source.client.get_image_url(message_id, new_photo.media_id)
        if not photo_url and new_photo.image_info.uri_map:
            photo_url = list(new_photo.image_info.uri_map.values())[-1]
        photo_id = self.get_photo_id(photo_url)
        if self.photo_id == photo_id:
            return
        self.photo_id = photo_id
        self.avatar_url, *_ = await self._reupload_fb_file(photo_url, source, sender.intent)
        try:
            event_id = await sender.intent.set_room_avatar(self.mxid, self.avatar_url)
        except IntentError:
            event_id = await self.main_intent.set_room_avatar(self.mxid, self.avatar_url)
        await self.save()
        await DBMessage(
            mxid=event_id,
            mx_room=self.mxid,
            index=0,
            timestamp=timestamp,
            fbid=message_id,
            fb_chat=self.fbid,
            fb_receiver=self.fb_receiver,
            fb_sender=sender.fbid,
            fb_txn_id=None,
        ).insert()
        await self.update_bridge_info()

    async def handle_facebook_name(
        self,
        source: u.User,
        sender: p.Puppet,
        new_name: str,
        message_id: str,
        timestamp: int,
    ) -> None:
        if self.name == new_name or message_id in self._dedup:
            return
        self._dedup.appendleft(message_id)
        self.name = new_name
        if not self.mxid or self.is_direct:
            return
        try:
            event_id = await sender.intent.set_room_name(self.mxid, self.name)
        except IntentError:
            event_id = await self.main_intent.set_room_name(self.mxid, self.name)
        await self.save()
        await DBMessage(
            mxid=event_id,
            mx_room=self.mxid,
            index=0,
            timestamp=timestamp,
            fbid=message_id,
            fb_chat=self.fbid,
            fb_receiver=self.fb_receiver,
            fb_sender=sender.fbid,
            fb_txn_id=None,
        ).insert()
        await self.update_bridge_info()

    async def handle_facebook_reaction_add(
        self,
        source: u.User,
        sender: p.Puppet | int,
        message_id: str,
        reaction: str,
        existing: DBReaction | None = None,
        target_message: DBMessage | None = None,
        timestamp: int | None = None,
    ) -> None:
        if isinstance(sender, int):
            sender = await p.Puppet.get_by_fbid(sender)
        dedup_id = f"react_{message_id}_{sender.fbid}_{reaction}"
        async with self.optional_send_lock(sender.fbid):
            if dedup_id in self._dedup:
                self.log.debug(f"Ignoring duplicate reaction from {sender.fbid} to {message_id}")
                return
            self._dedup.appendleft(dedup_id)

        if not existing:
            existing = await DBReaction.get_by_fbid(message_id, self.fb_receiver, sender.fbid)
            if existing and existing.reaction == reaction:
                self.log.debug(
                    f"Ignoring duplicate reaction from {sender.fbid} to {message_id} (db check)"
                )
                return

        if not await self._bridge_own_message_pm(source, sender, f"reaction to {message_id}"):
            return

        intent = sender.intent_for(self)

        if not target_message:
            target_message = await DBMessage.get_by_fbid(message_id, self.fb_receiver)
        if not target_message:
            self.log.debug(f"Ignoring reaction from {sender.fbid} to unknown message {message_id}")
            return

        timestamp = timestamp or int(time.time() * 1000)
        mxid = await intent.react(
            room_id=target_message.mx_room,
            event_id=target_message.mxid,
            key=variation_selector.add(reaction),
            timestamp=timestamp,
        )
        self.log.debug(f"{sender.fbid} reacted to {target_message.mxid} ({message_id}) -> {mxid}")

        await self._upsert_reaction(
            existing, intent, mxid, target_message, sender, reaction, timestamp
        )

    async def _upsert_reaction(
        self,
        existing: DBReaction | None,
        intent: IntentAPI,
        mxid: EventID,
        message: DBMessage,
        sender: u.User | p.Puppet,
        reaction: str,
        mx_timestamp: int,
    ) -> None:
        if existing:
            self.log.debug(
                f"_upsert_reaction redacting {existing.mxid} and inserting {mxid}"
                f" (message: {message.mxid})"
            )
            await intent.redact(existing.mx_room, existing.mxid)
            existing.reaction = reaction
            existing.mxid = mxid
            existing.mx_room = message.mx_room
            existing.mx_timestamp = mx_timestamp
            await existing.save()
        else:
            self.log.debug(f"_upsert_reaction inserting {mxid} (message: {message.mxid})")
            await DBReaction(
                mxid=mxid,
                mx_room=message.mx_room,
                fb_msgid=message.fbid,
                fb_receiver=self.fb_receiver,
                fb_sender=sender.fbid,
                reaction=reaction,
                mx_timestamp=mx_timestamp,
            ).insert()

    async def handle_facebook_reaction_remove(
        self, source: u.User, sender: p.Puppet | int, target: str | DBReaction
    ) -> None:
        if not self.mxid:
            return
        if isinstance(sender, int):
            sender = await p.Puppet.get_by_fbid(sender)
        if isinstance(target, DBReaction):
            reaction = target
        else:
            reaction = await DBReaction.get_by_fbid(target, self.fb_receiver, sender.fbid)
        if reaction:
            try:
                await sender.intent_for(self).redact(reaction.mx_room, reaction.mxid)
            except MForbidden:
                await self.main_intent.redact(reaction.mx_room, reaction.mxid)
            try:
                self._dedup.remove(f"react_{reaction.fb_msgid}_{sender.fbid}_{reaction.reaction}")
            except ValueError:
                pass
            await reaction.delete()

    async def handle_facebook_poll(
        self,
        sender: p.Puppet,
        thread_change: mqtt.ThreadChange,
    ) -> None:
        if not self.mxid:
            return

        if thread_change.action_data["event_type"] != "question_creation":
            return

        question_json = json.loads(thread_change.action_data.get("question_json"))
        options_html = "".join(f"<li>{o['text']}</li>" for o in question_json.get("options"))
        html = f"""
            <b>Poll: {question_json.get("text")}</b><br>
            Options:
            <ul>{options_html}</ul>
            Open Facebook Messenger to vote.
        """.strip()

        await self._send_message(
            sender.intent_for(self),
            TextMessageEventContent(
                msgtype=MessageType.TEXT,
                body=await parse_html(html),
                format=Format.HTML,
                formatted_body=html,
            ),
        )

    ringing: bool = False

    async def handle_facebook_call(self, sender: p.Puppet) -> None:
        if self.ringing:
            return

        self.ringing = True
        html = f"<b>Started a call.</b> Open Facebook Messenger to answer."
        await self._send_message(
            sender.intent_for(self),
            TextMessageEventContent(
                msgtype=MessageType.TEXT,
                body=await parse_html(html),
                format=Format.HTML,
                formatted_body=html,
            ),
        )

    async def handle_facebook_call_hangup(self) -> None:
        self.ringing = False

    async def handle_facebook_group_call(
        self, sender: p.Puppet, thread_change: mqtt.ThreadChange
    ) -> None:
        if not self.mxid:
            return

        if thread_change.action_data["event"] != "group_call_started":
            return

        call_type = "video" if thread_change.action_data["video"] == "1" else "audio"
        html = f"<b>Started a group {call_type} call.</b> Open Facebook Messenger to answer."

        await self._send_message(
            sender.intent_for(self),
            TextMessageEventContent(
                msgtype=MessageType.TEXT,
                body=await parse_html(html),
                format=Format.HTML,
                formatted_body=html,
            ),
        )

    async def handle_facebook_join(
        self, source: u.User, sender: p.Puppet, users: list[p.Puppet]
    ) -> None:
        sender_intent = sender.intent_for(self)
        for user in users:
            await sender_intent.invite_user(self.mxid, user.mxid)
            await user.intent_for(self).join_room_by_id(self.mxid)
            if not user.name:
                self.schedule_resync(source, user)

    async def handle_facebook_leave(
        self, source: u.User, sender: p.Puppet, removed: p.Puppet
    ) -> None:
        if sender == removed:
            await removed.intent_for(self).leave_room(self.mxid)
        else:
            try:
                await sender.intent_for(self).kick_user(self.mxid, removed.mxid)
            except MForbidden:
                await self.main_intent.kick_user(
                    self.mxid, removed.mxid, reason=f"Kicked by {sender.name}"
                )

    # endregion

    async def handle_forced_fetch(self, source: u.User, messages: list[graphql.Message]) -> None:
        most_recent = await DBMessage.get_most_recent(self.fbid, self.fb_receiver)
        for message in messages:
            puppet = await p.Puppet.get_by_fbid(message.message_sender.id)
            if message.timestamp > most_recent.timestamp:
                await self.handle_facebook_message(source, puppet, message)
            else:
                await self._try_handle_graphql_reactions(
                    source, message.message_id, message.message_reactions
                )

    # region Database getters

    async def postinit(self) -> None:
        self.by_fbid[self.fbid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self
        self._main_intent = (
            (await self.get_dm_puppet()).default_mxid_intent if self.is_direct else self.az.intent
        )

    @classmethod
    @async_getter_lock
    async def get_by_mxid(cls, mxid: RoomID) -> Portal | None:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_mxid(mxid))
        if portal:
            await portal.postinit()
            return portal

        return None

    @classmethod
    @async_getter_lock
    async def get_by_fbid(
        cls,
        fbid: int,
        *,
        fb_receiver: int = 0,
        create: bool = True,
        fb_type: ThreadType | None = None,
    ) -> Portal | None:
        if fb_type:
            fb_receiver = fb_receiver if fb_type == ThreadType.USER else 0
        fbid_full = (fbid, fb_receiver)
        try:
            return cls.by_fbid[fbid_full]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_fbid(fbid, fb_receiver))
        if portal:
            await portal.postinit()
            return portal

        if fb_type and create:
            portal = cls(fbid=fbid, fb_receiver=fb_receiver, fb_type=fb_type)
            await portal.insert()
            await portal.postinit()
            return portal

        return None

    @classmethod
    async def get_all_by_receiver(cls, fb_receiver: int) -> AsyncGenerator[Portal, None]:
        portals = await super().get_all_by_receiver(fb_receiver)
        portal: Portal
        for portal in portals:
            try:
                yield cls.by_fbid[(portal.fbid, portal.fb_receiver)]
            except KeyError:
                await portal.postinit()
                yield portal

    @classmethod
    async def all(cls) -> AsyncGenerator[Portal, None]:
        portals = await super().all()
        portal: Portal
        for portal in portals:
            try:
                yield cls.by_fbid[(portal.fbid, portal.fb_receiver)]
            except KeyError:
                await portal.postinit()
                yield portal

    @classmethod
    def get_by_thread(
        cls,
        key: graphql.ThreadKey | mqtt.ThreadKey,
        fb_receiver: int | None = None,
        create: bool = True,
    ) -> Awaitable[Portal]:
        return cls.get_by_fbid(
            key.id,
            fb_receiver=fb_receiver,
            create=create,
            fb_type=ThreadType.from_thread_key(key),
        )

    # endregion
