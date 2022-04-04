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
from typing import Any, Dict, List, Optional, Set
import base64
import json

from attr import dataclass
import attr

from maufbapi.thrift import RecursiveType, ThriftObject, TType, autospec, field
from mautrix.types import ExtensibleEnum, SerializableAttrs, SerializableEnum

from ..common import MessageUnsendability as Unsendability
from ..graphql import ExtensibleAttachment, MontageReplyData


@autospec
@dataclass
class ThreadKey(ThriftObject):
    other_user_id: int = field(TType.I64, default=None)
    thread_fbid: int = field(TType.I64, default=None)

    @property
    def id(self) -> Optional[int]:
        if self.other_user_id:
            return self.other_user_id
        elif self.thread_fbid:
            return self.thread_fbid
        else:
            return None


class ThreadReadStateEffect(ExtensibleEnum):
    MARK_READ = 1
    MARK_UNREAD = 2
    KEEP_AS_IS = 3


@autospec
@dataclass(kw_only=True)
class IGItemIDBlob(ThriftObject):
    first_64_bits: int = field(TType.I64)
    second_64_bits: int = field(TType.I64)

    @property
    def combine(self) -> int:
        return self.first_64_bits << 64 + self.second_64_bits


@autospec
@dataclass(kw_only=True)
class ConversationID(ThriftObject):
    conversation_fbid: int = field(TType.I64, default=None)
    canonical_participant_fbids: Set[int] = field(
        TType.SET, item_type=TType.I64, factory=lambda: {}
    )


@autospec
@dataclass(kw_only=True)
class MessageMetadata(ThriftObject):
    thread: ThreadKey
    id: str
    offline_threading_id: int = field(TType.I64, default=None)
    sender: int = field(TType.I64)  # actor_fbid
    timestamp: int = field(TType.I64)
    should_buzz_device: bool = False
    admin_text: str = field(default=None)
    tags: List[str] = field(factory=lambda: [])
    thread_read_state_effect: ThreadReadStateEffect = field(
        TType.I32, default=ThreadReadStateEffect.MARK_READ
    )
    skip_bump_thread: bool = False
    skip_snippet_update: bool = False
    message_unsendability: Unsendability = field(
        TType.BINARY, default=Unsendability.DENY_FOR_NON_SENDER
    )
    snippet: str = None
    # microseconds: int32?
    # index 15: undefined
    ig_item_id_blob: IGItemIDBlob = field(index=16, default=None)
    cid: ConversationID = None

    # data: struct(map) = field(index=1001)
    # folder_id: struct(system_folder_id: ??, user_folder_id: ??)
    # non_persistent_data: struct(map)


class ImageSource(ExtensibleEnum):
    UNKNOWN = 0
    FILE = 1
    QUICKCAM_FRONT = 2
    QUICKCAM_BACK = 3


@autospec
@dataclass(kw_only=True)
class ImageInfo(ThriftObject):
    original_width: int = field(TType.I32)
    original_height: int = field(TType.I32)
    uri_map: Dict[int, str] = field(
        TType.MAP,
        key_type=TType.I32,
        default=None,
        value_type=RecursiveType(TType.BINARY, python_type=str),
    )
    image_source: ImageSource = field(TType.I32, default=ImageSource.UNKNOWN)
    raw_image_uri: str = None
    raw_image_uri_format: str = None
    animated_uri_map: Dict[int, str] = field(
        TType.MAP,
        key_type=TType.I32,
        default=None,
        index=7,
        value_type=RecursiveType(TType.BINARY, python_type=str),
    )
    image_type: str = field(default=None)
    animated_image_type: str = field(default=None)
    render_as_sticker: bool = False
    mini_preview: bytes = None
    blurred_image_uri: str = None


class VideoSource(ExtensibleEnum):
    UNKNOWN = 0
    NON_QUICKCAM = 1
    QUICKCAM = 2
    SPEAKING_STICKER = 3
    RECORDED_STICKER = 4
    VIDEO_MAIL = 5


@autospec
@dataclass(kw_only=True)
class VideoInfo(ThriftObject):
    original_width: int = field(TType.I32)
    original_height: int = field(TType.I32)
    duration_ms: int = field(TType.I32)
    thumbnail_url: str
    download_url: str
    source: VideoSource = field(TType.I32, default=VideoSource.UNKNOWN)
    rotation: int = field(TType.I32, default=0)
    loop_count: int = field(TType.I32, default=0)


@autospec
@dataclass(kw_only=True)
class AudioInfo(ThriftObject):
    is_voicemail: bool
    call_id: str = None
    url: str
    duration_ms: int = field(TType.I32)
    sampling_frequency_hz: int = field(TType.I32)
    waveform: List[float] = field(TType.LIST, item_type=TType.FLOAT)
    # message_voice_transcription


@autospec
@dataclass(kw_only=True)
class Attachment(ThriftObject):
    media_id_str: str
    mime_type: str = field(default=None)
    file_name: str = field(default=None)
    media_id: int = field(TType.I64, default=None)  # fbid
    file_size: int = field(TType.I64, default=None)
    # attribution_info: struct
    extensible_media: str = field(default=None, index=7)
    # xma_graphql: str
    # blob_graphql: str
    image_info: ImageInfo = field(default=None, index=10)
    video_info: VideoInfo = field(default=None)
    audio_info: AudioInfo = field(default=None)
    # can contain a dash_manifest key with some XML as the value
    # or fbtype key with a number as value
    extra_metadata: Dict[str, str] = field(factory=lambda: {})
    # node_media_fbid: int64
    # raven_metadata: ???
    # client_attachment_type: ???
    # raven_poll_info: ???
    # generic_data_map: ???

    # haystack_handle: index 1000: str
    # generic_metadata: dict
    # hash: str
    # encryption_key: str
    # titan_type: int32
    # other_user_fbids: list
    # mercury_json: str
    # use_ref_counting: bool

    def parse_extensible(self) -> ExtensibleAttachment:
        if not self.extensible_media:
            raise ValueError("This attachment does not contain an extensible attachment")
        data = json.loads(self.extensible_media)
        raw_media_key = f"extensible_message_attachment:{self.media_id_str}"
        expected_key = base64.b64encode(raw_media_key.encode("utf-8")).decode("utf-8").rstrip("=")
        try:
            media_data = data[expected_key]
        except KeyError:
            media_data = list(data.values())[0]
        return ExtensibleAttachment.deserialize(media_data)


@autospec
@dataclass(kw_only=True)
class Reaction(ThriftObject):
    thread: ThreadKey
    message_id: str
    # index 3: unknown int32 (zero)
    reaction_sender_id: int = field(TType.I64, index=4)
    reaction: str = field(default=None)
    message_sender_id: int = field(TType.I64)
    # index 7: unknown number as string, similar to MessageMetadata's index 3


@dataclass
class PresenceInfo(SerializableAttrs):
    user_id: int = attr.ib(metadata={"json": "u"})
    status: int = attr.ib(metadata={"json": "p"})
    last_seen: int = attr.ib(metadata={"json": "l"}, default=0)


@dataclass
class Presence(SerializableAttrs):
    updates: List[PresenceInfo] = attr.ib(metadata={"json": "list"})
    list_type: str


class MentionType(SerializableEnum):
    PERSON = "p"
    THREAD = "t"


@dataclass
class Mention(SerializableAttrs):
    offset: int = attr.ib(metadata={"json": "o"})
    length: int = attr.ib(metadata={"json": "l"})
    user_id: str = attr.ib(metadata={"json": "i"})
    type: MentionType = attr.ib(metadata={"json": "t"}, default=MentionType.PERSON)


@autospec
@dataclass(kw_only=True)
class Message(ThriftObject):
    metadata: MessageMetadata
    text: str = field(default=None)
    # index 3: undefined
    sticker: int = field(TType.I64, index=4, default=None)
    attachments: List[Attachment] = field(factory=lambda: [])
    # index 6: some sort of struct:
    #    1: List[BinaryThreadKey]?
    #    2: ???
    #    3: timestamp?
    #    4: timestamp?
    extra_metadata: Dict[str, bytes] = field(index=7, factory=lambda: {})

    # iris_seq_id = field(TType.I64, index=1000)
    # generic_data_map: struct(map)
    # reply_to_message_id: str
    # message_reply:
    #   reply_to_message_id: struct(id: str)
    #   status: enum(VALID=0, DELETED, TEMPORARILY_UNAVAILABLE)
    #   reply_to_item_id:
    #     offline_threading_id: str = field(index=10)
    #     client_context: str
    #     item_id: IGItemIDBlob
    # request_context: index 1012: map<binary, binary>
    #   key apiArgs: binary containing thrift
    #     index 2: binary url, https://www.facebook.com/intern/agent/realtime_delivery/
    #     index 4: int64 (ex: 0)
    #     index 7: binary, empty?
    #     index 5: binary, some sort of uuid
    #     index 8: list<map>
    #       item 1: map<binary, binary>
    #         {"layer": "www", "push_phase": "C3", "www_rev": "1003179603",
    #          "buenopath": "XRealtimeDeliveryThriftServerController:sendRealtimeDeliveryRequest:/ls_req:TASK_LABEL=SEND_MESSAGE_V{N}"}
    #     index 9: binary (ex: www)
    #     index 10: boolean (ex: false)
    # random_nonce: int32
    # participants: list<???>
    # iris_tags: list<binary>
    # meta_tags: list<???>
    # tq_seq_id: int64 (ex: 924)

    @property
    def mentions(self) -> List[Mention]:
        return [
            Mention.deserialize(item) for item in json.loads(self.extra_metadata.get("prng", "[]"))
        ]

    @property
    def montage_reply_data(self) -> Optional[MontageReplyData]:
        data = self.extra_metadata.get("montage_reply_data")
        if not data:
            return None
        return MontageReplyData.parse_json(data)


@autospec
@dataclass(kw_only=True)
class ExtendedMessage(ThriftObject):
    reply_to_message: Message
    message: Message


@autospec
@dataclass(kw_only=True)
class UnsendMessage(ThriftObject):
    thread: ThreadKey
    message_id: str
    timestamp: int = field(TType.I64)
    user_id: int = field(TType.I64)
    # index 5: unknown int64 (ex: 0)


@autospec
@dataclass
class ExtendedAddMemberParticipant(ThriftObject):
    addee_user_id: int = field(TType.I64)
    adder_user_id: int = field(TType.I64)
    # index 3: unknown int32 (ex: 0)
    timestamp: int = field(TType.I64, index=4)


@autospec
@dataclass
class ExtendedAddMember(ThriftObject):
    thread: ThreadKey
    users: List[ExtendedAddMemberParticipant]


@autospec
@dataclass
class MessageSyncClientEvent(ThriftObject):
    # 1: deltaAdminAddedToGroupThread
    # 2: deltaAdminRemovedFromGroupThread
    # 3: deltaJoinableMode
    # 4: deltaApprovalMode
    # 5: deltaApprovalQueue
    # 6: deltaRtcCallData
    # 7: deltaGroupThreadDescription
    # 8: liveLocationData
    # 9: deltaPaymentPinProtectionStatusData
    reaction: Reaction = field(index=10, default=None)
    # 11: deltaRoomDiscoverableMode
    # 12: deltaThreadSnapshot
    # 13: deltaMediaUpdatesData
    # 14: deltaMuteThreadReactions
    # 15: deltaMuteThreadMentions
    # 16: deltaOmniMDirectives
    # 17: deltaMontageParticipantsUpdate
    # 18: deltaVideoRoomMode
    # 19: deltaRoomAssociatedObjectUpdate
    # 20: deltaPlatformUpdatesData
    # 21: deltaInboxUnitUpdate
    # 22: deltaReadReceipt
    # 23: deltaMontageMessageReactions
    # 24: deltaNewMontageMessage
    # 25: deltaMontageMessageDelete
    # 26: deltaMontageThreadDelete
    # 27: deltaMontageThreadForcedFetch
    # 28: deltaMontageReadReceipt
    # 29: deltaMontageMarkRead
    # 30: deltaUpdatePrivateGroupJoinableLink
    # 31: deltaActivityTabUpdatesData
    # 32: deltaAdContext
    # 33: deltaThreadStreak
    # 34: deltaChangeViewerStatus
    # 35: deltaUpdateGroupEventRSVPStatus
    # 36: deltaPageThreadFollowUpData
    # 37: deltaMontageDirectOpen
    # 38: deltaMontageDirectExpire
    # 39: deltaThreadActivityNotification
    # 40: deltaMontageDirectKeep
    # 41: deltaMuteThreadGames
    # 42: deltaPageThreadSubtitleUpdate
    # ??? extended_add_member: ExtendedAddMember = field(index=42, default=None)
    # 43: deltaUpdateNotifiedChatsReadTimestamp
    # 44: deltaPeopleTabUpdatesData
    # 45: deltaUpdateEmojiStatus
    # 46: deltaUpdateFirstUnopenedMontageDirect
    # 47: deltaUpdateGroupsSyncStatus
    # 48: deltaUpdateGroupsSyncMetadata
    # 49: deltaGroupApprovalMuteSetting
    # 50: deltaPendingFolderCountChange
    # 51: deltaOmniMDirectivesV2
    # 52: deltaParticipantsSubscribeMetadata
    # 53: deltaAdminModelStatusUpdate
    # 54: deltaMessengerCYMKData
    extended_message: ExtendedMessage = field(index=55, default=None)  # deltaMessageReply
    # 56: deltaUpdateVideoChatLink
    # 57: deltaUpdateThreadTheme
    # 58: deltaUpdatePinnedThread
    # 59: deltaRTCSignalingMessage
    # 60: deltaPageThreadAssignedAdminUpdate
    # 61: deltaPromoteGroupThreadAdmin
    # 62: deltaMessageTranslation
    # 63: deltaMessagingFolderSettingUpdate
    # 64: deltaThreadConnectivityStatusUpdate
    # 65: deltaMessengerThreadActivityBannerUpdate
    # 66: deltaLivingRoomStatusUpdate
    unsend_message: UnsendMessage = field(index=67, default=None)  # deltaRecallMessageData
    # 68: deltaMentorshipUpdate
    # 69: deltaPageUnSubscribeStatus
    # 70: deltaRTCMultiwayMessage
    # 71: deltaMessengerRelationshipEventEligibilityUpdate
    # 72: deltaSchoolChatShouldShowInviteScreenUpdate
    # 73: deltaMessengerAdsConversionUpdate
    # 74: deltaUpdateSavedMessage
    # 75: deltaTweensAnswerWouldYouRather
    # 76: deltaMessageVoiceTranscription
    # 77: deltaPageBlurredImageStatus
    # 78: deltaHideMessageForMessengerKidsData
    # 79: deltaWorkChatSuggestions
    # 80: deltaUpdatePinnedMessage
    # 81: deltaGlobalMute
    # 82: deltaSwitchAccountBadgingUpdated
    # 83: deltaUpdateThreadDisappearingMode
    # 84: deltaPolicyViolation
    # 85: deltaMessengerBusinessSuggestedReplyUpdate
    # 86: deltaMuteCallsFromThread
    # 87: deltaNewRavenMessage
    # 88: deltaRavenAction
    # 89: deltaNewFriendBumpSeen
    # 90: deltaUpdateThreadEmoji
    # 91: deltaGlobalNotificationSettingControl
    # 92: deltaGlobalNewFriendBumpSetting
    # 93: deltaMessagingReachabilitySettingUpdate
    # 94: deltaGlobalReplyReminderSetting
    # 95: deltaMessagePowerUp
    # 96: deltaRtcRoomData
    # 97: deltaBiiMSavedRepliesData
    # 98: deltaGlobalMessageReminderSetting
    # 99: deltaUpdateThreadSnippet
    # 100: deltaUpdateMagicWords
    # 101: deltaSecondaryLanguageBody
    # 102: deltaSoundBite
    # 103: deltaGroupThreadNotifSettings
    # 104: deltaMessengerGroupThreadWarning
    # 105: deltaLastMissedCallData
    # 106: deltaAcceptGroupThread
    # 107: deltaBiiMPageMessageNotification
    # 109: deltaParticipantSpecialThreadRole
    # 110: deltaUpdatePinnedMessagesV2
    # 111: deltaIsAllUnreadMessageMissedCallXma
    # 112: deltaThreadCutoverData
    # 113: deltaInboxPageMessageNotification
    # 115: deltaRemoveMessage


@autospec
@dataclass
class MessageSyncClientPayload(ThriftObject):
    items: List[MessageSyncClientEvent]


@autospec
@dataclass(kw_only=True)
class MessageSyncClientWrapper(ThriftObject):
    data: bytes

    def parse(self) -> MessageSyncClientPayload:
        return MessageSyncClientPayload.from_thrift(self.data)


@autospec
@dataclass
class ReadReceipt(ThriftObject):
    thread: ThreadKey
    user_id: int = field(TType.I64)
    read_at: int = field(TType.I64)
    read_to: int = field(TType.I64)


@autospec
@dataclass
class OwnReadReceipt(ThriftObject):
    threads: List[ThreadKey]
    # index 2: ???
    read_to: int = field(TType.I64, index=3)
    read_at: int = field(TType.I64)


@autospec
@dataclass
class NameChange(ThriftObject):
    metadata: MessageMetadata
    new_name: str


@autospec
@dataclass
class AvatarChange(ThriftObject):
    metadata: MessageMetadata
    new_avatar: Attachment


class ThreadChangeAction(ExtensibleEnum):
    # action_data:
    #   'thread_icon_url': 'https://www.facebook.com/images/emoji.php/v9/t54/1/16/1f408.png'
    #   'thread_icon': '🐈'
    ICON = "change_thread_icon"

    # action_data:
    #   'should_show_icon': '1'
    #   'theme_color': 'FF5E007E'
    #   'accessibility_label': 'Grape'
    THEME = "change_thread_theme"

    # action_data:
    #   'THREAD_CATEGORY': 'GROUP'
    #   'TARGET_ID': '<user id>'
    #   'ADMIN_TYPE': '0'
    #   'ADMIN_EVENT': 'add_admin' or 'remove_admin'
    ADMINS = "change_thread_admins"

    # action_data:
    #   'APPROVAL_MODE': '1' (or '0'?)
    #   'THREAD_CATEGORY': 'GROUP'
    APPROVAL_MODE = "change_thread_approval_mode"

    # action_data:
    #   'nickname': '<per-room displayname>'
    #   'participant_id': '<user id>'
    NICKNAME = "change_thread_nickname"


@autospec
@dataclass(kw_only=True)
class ThreadChange(ThriftObject):
    metadata: MessageMetadata
    action: ThreadChangeAction = field(TType.BINARY)
    action_data: Dict[str, str] = field(
        TType.MAP,
        key_type=RecursiveType(TType.BINARY, python_type=str),
        value_type=RecursiveType(TType.BINARY, python_type=str),
    )


@autospec
@dataclass(kw_only=True)
class AddMemberParticipant(ThriftObject):
    id: int = field(TType.I64)
    first_name: str
    name: str
    # index 4: unknown boolean


@autospec
@dataclass(kw_only=True)
class AddMember(ThriftObject):
    metadata: MessageMetadata
    users: List[AddMemberParticipant]


@autospec
@dataclass(kw_only=True)
class RemoveMember(ThriftObject):
    metadata: MessageMetadata
    user_id: int = field(TType.I64)


@autospec
@dataclass(kw_only=True)
class DeliveryReceipt(ThriftObject):
    thread: ThreadKey
    user_id: Optional[int] = field(TType.I64, default=None)
    # indices 3-5: ???
    message_id_list: List[str] = field(index=6)
    timestamp: int = field(TType.I64)


@autospec
@dataclass(kw_only=True)
class ForcedFetch(ThriftObject):
    thread: ThreadKey
    # index 2: ???
    # index 3: unknown bool (False)


@autospec
@dataclass(kw_only=True)
class MessageSyncEvent(ThriftObject):
    # 1: no_op: struct (no fields)
    message: Message = field(index=2, default=None)
    # 3: new_group_thread
    own_read_receipt: OwnReadReceipt = field(index=4, default=None)  # deltaMarkRead
    # 5: mark_unread
    # 6: message_delete
    # 7: thread_delete
    add_member: AddMember = field(index=8, default=None)  # deltaParticipantsAddedToGroupThread
    remove_member: RemoveMember = field(index=9, default=None)  # deltaParticipantLeftGroupThread
    name_change: NameChange = field(index=10, default=None)
    avatar_change: AvatarChange = field(index=11, default=None)
    # 12: mute_settings
    # 13: thread_action
    # 14: thread_folder
    # 15: rtc_event_log
    # 16: video_call
    thread_change: ThreadChange = field(index=17, default=None)  # deltaAdminTextMessage
    forced_fetch: ForcedFetch = field(index=18, default=None)
    read_receipt: ReadReceipt = field(index=19, default=None)  # deltaReadReceipt
    # 20: broadcast_message
    # 21: mark_folder_seen
    # 22: sent_message
    # 23: pinned_groups
    # 24: page_admin_reply
    delivery_receipt: DeliveryReceipt = field(index=25, default=None)
    # 26: p2p_payment_message
    # 27: folder_count
    # 28: pages_manager_event
    # 29: notification_settings
    # 30: replace_message
    # 31: zero_rating
    # 32: montage_message
    # 33: genie_message
    # 34: generic_map_mutation
    # 35: admin_added
    # 36: admin_removed
    # 37: rtc_call_data
    # 38: joinable_mode
    # 39: approval_mode
    # 40: approval_queue
    # 41: amend_message
    client_payload: MessageSyncClientWrapper = field(index=42, default=None)
    # 43: non_persisted_payload
    # 44: group_history
    # 45: group_subscribe_metadata_sync
    # 46: create_new_user
    # 47: recall_message
    # 48: mutate_message_tags
    # 49: set_thread_metadata
    # 50: thread_history_delete
    # 51: new_raven_message
    # 52: demote_interop_thread
    # 53: ig_mark_thread_unread
    # 54: all_participants_removed
    # 55: create_reaction
    # 56: delete_reaction
    # 57: add_poll_vote
    # 58: raven_action
    # 59: create_one_on_one_thread
    # 1001: change_mailbox_status
    # 1002: global_message_delete

    def get_parts(self) -> List[Any]:
        parts = [
            self.message,
            self.own_read_receipt,
            self.add_member,
            self.remove_member,
            self.name_change,
            self.avatar_change,
            self.thread_change,
            self.forced_fetch,
            self.read_receipt,
            self.delivery_receipt,
        ]
        if self.client_payload:
            for inner_item in self.client_payload.parse().items:
                parts += [
                    inner_item.reaction,
                    inner_item.extended_message,
                    inner_item.unsend_message,
                ]
        return [part for part in parts if part is not None]


class MessageSyncError(ExtensibleEnum):
    QUEUE_OVERFLOW = "ERROR_QUEUE_OVERFLOW"
    QUEUE_UNDERFLOW = "ERROR_QUEUE_UNDERFLOW"
    QUEUE_NOT_FOUND = "ERROR_QUEUE_NOT_FOUND"


@autospec
@dataclass(kw_only=True)
class MessageSyncPayload(ThriftObject):
    items: List[MessageSyncEvent] = field(factory=lambda: [])
    first_seq_id: int = field(TType.I64, default=None)
    last_seq_id: int = field(TType.I64, default=None)
    viewer: int = field(TType.I64, default=None)
    # indices 5-10: ???
    subscribe_ok: str = field(index=11, default=None)
    error: MessageSyncError = field(TType.BINARY, default=None)


@autospec
@dataclass(kw_only=True)
class SendMessageResponse(ThriftObject):
    offline_threading_id: int = field(TType.I64)
    success: bool
    # index 3: unknown i32 present for errors
    error_message: str = field(default=None, index=4)
    # index 5: unknown boolean present for errors


@autospec
@dataclass
class RegionHint(ThriftObject):
    code: str


@autospec
@dataclass(kw_only=True)
class RegionHintPayload(ThriftObject):
    unknown_int64: int = field(TType.I64)
    region_hint_data: bytes

    @property
    def region_hint(self) -> RegionHint:
        return RegionHint.from_thrift(self.region_hint_data)


@autospec
@dataclass
class TypingNotification(ThriftObject):
    user_id: int = field(TType.I64)
    typing_status: int = field(TType.I32)
