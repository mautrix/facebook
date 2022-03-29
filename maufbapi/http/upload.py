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

import base64
import hashlib
import json
import time
import unicodedata

from ..types import UploadResponse
from .base import BaseAndroidAPI


class UploadAPI(BaseAndroidAPI):
    async def send_media(
        self,
        data: bytes,
        file_name: str,
        mimetype: str,
        offline_threading_id: int,
        chat_id: int | None = None,
        is_group: bool | None = None,
        timestamp: int | None = None,
        reply_to: str | None = None,
        caption: str | None = None,
        duration: int | None = None,
    ) -> UploadResponse:
        # Convert file name to ASCII with some basic accent removal
        ascii_file_name = (
            unicodedata.normalize("NFKD", file_name).encode("ascii", "ignore").decode("ascii")
        )
        headers = {
            **self._headers,
            "accept-encoding": "x-fb-dz;d=1, gzip, deflate",
            "app_id": self.state.application.client_id,
            "device_id": self.state.device.uuid,
            "attempt_id": str(offline_threading_id),
            "offset": "0",
            "x-entity-length": str(len(data)),
            "x-entity-name": ascii_file_name,
            "x-entity-type": mimetype,
            "content-type": "application/octet-stream",
            "client_tags": json.dumps(
                {
                    "trigger": "2:thread_list:thread",
                    "is_in_chatheads": "false",
                    "is_in_bubbles": "false",
                }
            ),
            "original_timestamp": str(timestamp or int(time.time() * 1000)),
            "x-msgr-region": self.state.session.region_hint,
            "x-fb-friendly-name": "post_resumable_upload_session",
        }
        if chat_id:
            headers["thread_key_type"] = "GROUP" if is_group else "ONE_TO_ONE"
            headers["send_message_by_server"] = "1"
            headers["sender_fbid"] = str(self.state.session.uid)
            headers["to"] = f"tfbid_{chat_id}" if is_group else str(chat_id)
            headers["offline_threading_id"] = str(offline_threading_id)
            headers["ttl"] = "0"
            if reply_to:
                headers["replied_to_message_id"] = reply_to
            if caption:
                headers["caption"] = base64.b64encode(caption.encode("utf-8")).decode("ascii")
        else:
            headers["send_message_by_server"] = "0"
            headers["thread_type_hint"] = "thread"
        if mimetype.startswith("image/"):
            path_type = "messenger_gif" if mimetype == "image/gif" else "messenger_image"
            headers["image_type"] = "FILE_ATTACHMENT"
            headers["media_type"] = "PHOTO"
            headers["media_send_type"] = "PICK"
        elif mimetype.startswith("video/"):
            path_type = "messenger_video"
            headers["video_type"] = "FILE_ATTACHMENT"
            headers["media_type"] = "VIDEO"
            headers["media_send_type"] = "PICK"
        elif mimetype.startswith("audio/"):
            path_type = "messenger_audio"
            headers["audio_type"] = "VOICE_MESSAGE"
            headers["media_type"] = "AUDIO"
            headers["media_send_type"] = "CAPTURE"
            headers["is_voicemail"] = "0"
            if duration:
                headers["duration"] = str(duration)
        else:
            path_type = "messenger_file"
            headers["file_type"] = "FILE_ATTACHMENT"
            headers["media_type"] = "FILE"
            headers["media_send_type"] = "PICK"

        self.log.trace("Sending upload with headers: %s", headers)
        file_id = hashlib.md5(data).hexdigest() + str(offline_threading_id)
        resp = await self.http.post(
            self.rupload_url / path_type / file_id, headers=headers, data=data
        )
        json_data = await self._handle_response(resp)
        self.log.trace("Upload response: %s %s", resp.status, json_data)
        return UploadResponse.deserialize(json_data)
