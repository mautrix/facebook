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
from typing import Optional
import hashlib
import time
import json

from .base import BaseAndroidAPI
from ..types import UploadResponse


class UploadAPI(BaseAndroidAPI):
    async def send_media(self, data: bytes, file_name: str, mimetype: str,
                         chat_id: int, is_group: bool, offline_threading_id: int,
                         timestamp: Optional[int] = None) -> UploadResponse:
        headers = {
            **self._headers,
            "app_id": self.state.application.client_id,
            "device_id": self.state.device.uuid,
            "attempt_id": str(offline_threading_id),
            "offline_threading_id": str(offline_threading_id),
            "send_message_by_server": "1",
            "ttl": "0",
            "offset": "0",
            "to": f"tfbid_{chat_id}" if is_group else str(chat_id),
            "x-entity-length": str(len(data)),
            "x-entity-name": file_name,
            "x-entity-type": mimetype,
            "content-type": "application/octet-stream",
            "client_tags": json.dumps({"trigger": "2:thread_list:thread",
                                       "is_in_chatheads": "false"}),
            "original_timestamp": str(timestamp or int(time.time() * 1000)),
            "sender_fbid": str(self.state.session.uid),
            "x-fb-rmd": "state=NO_MATCH",
            "x-msgr-region": self.state.session.region_hint,
            "x-fb-friendly-name": "post_resumable_upload_session",
        }
        if mimetype.startswith("image/"):
            headers["image_type"] = "FILE_ATTACHMENT"
        elif mimetype.startswith("video/"):
            headers["video_type"] = "FILE_ATTACHMENT"
        elif mimetype.startswith("audio/"):
            headers["audio_type"] = "VOICE_MESSAGE"
            headers["is_voicemail"] = "0"
        file_id = hashlib.md5(data).hexdigest() + str(offline_threading_id)
        resp = await self.http.post(self.rupload_url / "messenger_image" / file_id,
                                    headers=headers, data=data)
        json_data = await self._handle_response(resp)
        self.log.trace("Upload response: %s %s", resp.status, json_data)
        parsed = UploadResponse.deserialize(json_data)
        return parsed
