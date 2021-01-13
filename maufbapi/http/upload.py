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

from .base import BaseAndroidAPI
from ..types import PasswordKeyResponse


class UploadAPI(BaseAndroidAPI):
    async def send_media(self, data: bytes, file_name: str, mimetype: str,
                         chat_id: int, is_group: bool, offline_threading_id: int,
                         timestamp: Optional[int] = None) -> PasswordKeyResponse:
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
            "x-entity-length": len(data),
            "x-entity-name": file_name,
            "x-entity-type": mimetype,
            # TODO shared enum with graphql attachment response
            "image_type": "FILE_ATTACHMENT",
            "content-type": "application/octet-stream",
            "original_timestamp": timestamp or int(time.time() * 1000),
            "sender_fbid": self.state.session.uid,
            "x-fb-rmd": "state=NO_MATCH",
            "x-msgr-region": self.state.session.region_hint,
            "x-fb-friendly-name": "post_resumable_upload_session",
        }
        # TODO generate 51-character hex id?
        file_id = hashlib.md5(data).hexdigest() + str(offline_threading_id)
        resp = await self.http.post(self.rupload_url / "messenger_image" / file_id,
                                    headers=headers, data=data)
        json_data = await self._handle_response(resp)
        parsed = PasswordKeyResponse.deserialize(json_data)
        self.state.session.password_encryption_pubkey = parsed.public_key
        self.state.session.password_encryption_key_id = parsed.key_id
        return parsed
