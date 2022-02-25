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
from typing import Dict, List

from attr import dataclass

from maufbapi.thrift import ThriftObject, TType, autospec, field


@autospec
@dataclass(kw_only=True)
class SendMessageRequest(ThriftObject):
    # tfbid_<groupid> for groups, plain user id for users
    chat_id: str  # to
    message: str  # body
    offline_threading_id: int = field(TType.I64)
    # coordinates: struct(accuracy: str, latitude: str, longitude: str)

    # Example values:
    #   'is_in_chatheads': 'false'
    #   'ld': '{"u":1674434.........}'
    #   'entrypoint': 'messenger_inbox:in_thread'
    #   'trigger': '2:thread_list:thread' or 'thread_view_messages_fragment_unknown'
    #   'active_now': '{"is_online":"false","last_active_seconds":"1431"}'
    #
    #   'media_camera_mode': 'VIDEO'
    #   'trigger': 'thread_view_messages_fragment_unknown'
    #   'entry_point': 'THREAD_CAMERA_COMPOSER_BUTTON'
    client_tags: Dict[str, str] = field(index=5, factory=lambda: {})
    # 369239263222822 = "like"
    sticker: str = field(default=None)  # object_attachment
    # copy_message_id: str
    # copy_attachment_id: str
    media_ids: List[str] = field(default=None, index=9)  # media_attachment_ids
    # fb_trace_meta: str
    # image_type: struct(value: int)
    sender_fbid: int = field(TType.I64, index=12)
    # broadcast_recipients: dict
    # attribution_app_id: int = field(TType.I64)
    # ios_bundle_id: str
    # android_key_hash: str
    # location_attachment: struct(coordinates: struct, isCurrentLocation: bool, placeId: int64)
    ttl: int = field(TType.I32, index=18, default=0)
    # ref_code: int = field(TType.I32)
    extra_metadata: Dict[str, str] = field(index=20, default=None)  # generic_metadata
    mark_read_watermark_timestamp: int = field(TType.I64, index=21, default=0)
    # attempt_id: str
    is_dialtone: bool = field(TType.BOOL, index=23, default=True)
    msg_attempt_id: int = field(TType.I64, index=24)
    # external_attachment_url: str
    # skip_android_hash_check: bool
    # original_copy_message_id: str
    reply_to: str = field(index=28, default=None)  # reply_to_message_id
    # log_info: dict
    # is_self_forwarded: bool
    # message_powerup_data: struct(powerUpStyle: enum(NONE=0, LOVE, GIFT_WRAP, CELEBRATION, FIRE))
    # sound_bite_id: int = field(TType.I64)
    # forward_score: int = field(TType.I32)
    # is_forwarded: bool


@autospec
@dataclass(kw_only=True)
class MarkReadRequest(ThriftObject):
    receipt_type: str = "read"
    state: bool = True
    # thread_id: str
    # action_id: int64
    # sync_seq_id: int64
    group_id: int = field(TType.I64, index=6, default=None)  # thread_fbid
    user_id: int = field(TType.I64, default=None)  # other_user_fbid
    # actor_fbid: int64
    read_to: int = field(TType.I64, index=9)  # watermark_timestamp
    # titan_originated_thread_id: str
    # should_send_read_receipt: bool
    # ad_page_message_type: str
    offline_threading_id: int = field(TType.I64, index=13)  # attempt_id


@autospec
@dataclass
class ChatIDWrapper(ThriftObject):
    chat_id: str


@autospec
@dataclass
class OpenedThreadRequest(ThriftObject):
    unknown_i64: int = field(TType.I64, default=0)
    _chat_id: bytes = field(default=None)

    @property
    def chat_id(self) -> int:
        return int(ChatIDWrapper.from_thrift(self._chat_id).chat_id)

    @chat_id.setter
    def chat_id(self, value: int) -> None:
        self._chat_id = ChatIDWrapper(str(value)).to_thrift()


@autospec
@dataclass
class SetTypingRequest(ThriftObject):
    user_id: int = field(TType.I64)  # recipient
    own_id: int = field(TType.I64)  # sender
    typing_status: int = field(TType.I32)  # state
    # attribution: struct(extension_type: str, generic_attribution_type: str, in_thread_app_id: int64, page_id: int64)
    # thread_key: struct(thread_id: str, thread_type: enum(CANONICAL=0, GROUP=1))


@autospec
@dataclass(kw_only=True)
class ResumeQueueRequest(ThriftObject):
    sync_token: str = None
    last_seq_id: int = field(TType.I64)
    max_deltas_able_to_process: int = field(TType.I32, default=None)
    delta_batch_size: int = field(TType.I32, default=None)
    encoding: str = None
    queue_type: str = None
    sync_api_version: int = field(TType.I64)
    device_id: str = None
    device_params: str = None
    queue_params: str
    entity_fbid: int = field(TType.I64, default=None)
    sync_token_long: int = field(TType.I64, index=12, default=1)
    trace_id: str = None
