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
from typing import List, Optional
from uuid import uuid4

from mautrix.types import JSON

from .base import BaseAndroidAPI
from .login import LoginAPI
from .upload import UploadAPI

from ..types import (ThreadListResponse, ThreadListQuery, MessageList, MoreMessagesQuery,
                     FetchStickersWithPreviewsQuery, StickerPreviewResponse, MessageUndoSend,
                     MessageUnsendResponse, ReactionAction, MessageReactionMutation)


class AndroidAPI(LoginAPI, UploadAPI, BaseAndroidAPI):
    async def fetch_threads(self, **kwargs) -> ThreadListResponse:
        return await self.graphql(ThreadListQuery(**kwargs), response_type=ThreadListResponse,
                                  path=["data", "viewer", "message_threads"])

    async def fetch_messages(self, thread_id: int, before_time_ms: int, **kwargs
                             ) -> MessageList:
        return await self.graphql(MoreMessagesQuery(thread_id=str(thread_id),
                                                    before_time_ms=str(before_time_ms), **kwargs),
                                  path=["data", "message_thread", "messages"],
                                  response_type=MessageList)

    async def fetch_stickers(self, ids: List[int], **kwargs) -> StickerPreviewResponse:
        kwargs["sticker_ids"] = [str(id) for id in ids]
        return await self.graphql(FetchStickersWithPreviewsQuery(**kwargs),
                                  path=["data"], response_type=StickerPreviewResponse, b=True)

    async def unsend(self, message_id: str) -> MessageUnsendResponse:
        return await self.graphql(MessageUndoSend(message_id=message_id,
                                                  client_mutation_id=str(uuid4()),
                                                  actor_id=str(self.state.session.uid)),
                                  path=["data", "message_undo_send"],
                                  response_type=MessageUnsendResponse)

    async def react(self, message_id: str, reaction: Optional[str]) -> None:
        action = ReactionAction.ADD if reaction else ReactionAction.REMOVE
        await self.graphql(MessageReactionMutation(message_id=message_id, reaction=reaction,
                                                   action=action, client_mutation_id=str(uuid4()),
                                                   actor_id=str(self.state.session.uid)),
                           response_type=JSON)
