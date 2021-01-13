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
from .base import BaseAndroidAPI
from .login import LoginAPI
from .upload import UploadAPI

from ..types import ThreadListResponse, ThreadListQuery, MessageList, MoreMessagesQuery


class AndroidAPI(LoginAPI, UploadAPI, BaseAndroidAPI):
    async def fetch_threads(self, **kwargs) -> ThreadListResponse:
        return await self.graphql(ThreadListQuery(**kwargs), response_type=ThreadListResponse,
                                  path=["data", "viewer", "message_threads"])

    async def fetch_messages(self, thread_id: str, before_time_ms: int, **kwargs
                             ) -> MessageList:
        return await self.graphql(MoreMessagesQuery(thread_id=str(thread_id),
                                                    before_time_ms=str(before_time_ms), **kwargs),
                                  path=["data", "message_thread", "messages"],
                                  response_type=MessageList)
