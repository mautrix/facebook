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
from typing import Optional, Union
import hashlib
import time
import json

from .base import BaseAndroidAPI
from ..types import UploadResponse


class UploadAPI(BaseAndroidAPI):
    async def send_media(self, data: bytes, file_name: str, mimetype: str,
                         offline_threading_id: int, chat_id: Optional[int] = None,
                         is_group: Optional[bool] = None, timestamp: Optional[int] = None,
                         reply_to: Optional[str] = None) -> UploadResponse:
        headers = {
            **self._headers,
            "app_id": self.state.application.client_id,
            "device_id": self.state.device.uuid,
            "attempt_id": str(offline_threading_id),
            "offset": "0",
            "x-entity-length": str(len(data)),
            "x-entity-name": file_name,
            "x-entity-type": mimetype,
            "content-type": "application/octet-stream",
            "client_tags": json.dumps({"trigger": "2:thread_list:thread",
                                       "is_in_chatheads": "false"}),
            "original_timestamp": str(timestamp or int(time.time() * 1000)),
            "x-fb-rmd": "state=NO_MATCH",
            "x-msgr-region": self.state.session.region_hint,
            "x-fb-friendly-name": "post_resumable_upload_session",
        }
        if chat_id:
            headers["send_message_by_server"] = "1"
            headers["sender_fbid"] = str(self.state.session.uid)
            headers["to"] = f"tfbid_{chat_id}" if is_group else str(chat_id)
            headers["offline_threading_id"] = str(offline_threading_id)
            headers["ttl"] = "0"
            if reply_to:
                headers["replied_to_message_id"] = reply_to
        else:
            headers["send_message_by_server"] = "0"
            headers["thread_type_hint"] = "thread"
        if mimetype.startswith("image/"):
            path_type = "messenger_gif" if mimetype == "image/gif" else "messenger_image"
            headers["image_type"] = "FILE_ATTACHMENT"
        elif mimetype.startswith("video/"):
            path_type = "messenger_video"
            headers["video_type"] = "FILE_ATTACHMENT"
        elif mimetype.startswith("audio/"):
            path_type = "messenger_audio"
            headers["audio_type"] = "VOICE_MESSAGE"
            headers["is_voicemail"] = "0"
        else:
            path_type = "messenger_file"
            headers["file_type"] = "FILE_ATTACHMENT"

        file_id = hashlib.md5(data).hexdigest() + str(offline_threading_id)
        resp = await self.http.post(self.rupload_url / path_type / file_id,
                                    headers=headers, data=data)
        json_data = await self._handle_response(resp)
        self.log.trace("Upload response: %s %s", resp.status, json_data)
        return UploadResponse.deserialize(json_data)
