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

from uuid import uuid4

from yarl import URL
import attr

from mautrix.types import JSON

from ..types import (
    DownloadImageFragment,
    FbIdToCursorQuery,
    FetchStickersWithPreviewsQuery,
    FileAttachmentUrlQuery,
    FileAttachmentURLResponse,
    ImageFragment,
    MessageList,
    MessageReactionMutation,
    MessageUndoSend,
    MessageUnsendResponse,
    MoreMessagesQuery,
    ReactionAction,
    SearchEntitiesNamedQuery,
    SearchEntitiesResponse,
    StickerPreviewResponse,
    SubsequentMediaQuery,
    SubsequentMediaResponse,
    ThreadListQuery,
    ThreadListResponse,
    ThreadQuery,
    ThreadQueryResponse,
)
from ..types.graphql import OwnInfo, PageInfo, Thread, ThreadMessageID
from .base import BaseAndroidAPI
from .login import LoginAPI
from .post_login import PostLoginAPI
from .upload import UploadAPI


class AndroidAPI(LoginAPI, PostLoginAPI, UploadAPI, BaseAndroidAPI):
    _file_url_cache: dict[ThreadMessageID, FileAttachmentURLResponse]

    async def fetch_thread_list(self, **kwargs) -> ThreadListResponse:
        return await self.graphql(
            ThreadListQuery(**kwargs),
            response_type=ThreadListResponse,
            path=["data", "viewer", "message_threads"],
        )

    async def fetch_thread_info(self, *thread_ids: str | int, **kwargs) -> list[Thread]:
        resp = await self.graphql(
            ThreadQuery(thread_ids=[str(i) for i in thread_ids], **kwargs),
            path=["data"],
            response_type=ThreadQueryResponse,
        )
        return resp.message_threads

    async def fetch_messages(self, thread_id: int, before_time_ms: int, **kwargs) -> MessageList:
        return await self.graphql(
            MoreMessagesQuery(
                thread_id=str(thread_id), before_time_ms=str(before_time_ms), **kwargs
            ),
            path=["data", "message_thread", "messages"],
            response_type=MessageList,
        )

    async def fetch_stickers(self, ids: list[int], **kwargs) -> StickerPreviewResponse:
        kwargs["sticker_ids"] = [str(id) for id in ids]
        return await self.graphql(
            FetchStickersWithPreviewsQuery(**kwargs),
            path=["data"],
            response_type=StickerPreviewResponse,
            b=True,
        )

    async def unsend(self, message_id: str) -> MessageUnsendResponse:
        return await self.graphql(
            MessageUndoSend(
                message_id=message_id,
                client_mutation_id=str(uuid4()),
                actor_id=str(self.state.session.uid),
            ),
            path=["data", "message_undo_send"],
            response_type=MessageUnsendResponse,
        )

    async def react(self, message_id: str, reaction: str | None) -> None:
        action = ReactionAction.ADD if reaction else ReactionAction.REMOVE
        await self.graphql(
            MessageReactionMutation(
                message_id=message_id,
                reaction=reaction,
                action=action,
                client_mutation_id=str(uuid4()),
                actor_id=str(self.state.session.uid),
            ),
            response_type=JSON,
        )

    async def fetch_image(self, media_id: int | str) -> ImageFragment:
        return await self.graphql(
            DownloadImageFragment(fbid=str(media_id)),
            path=["data", "node"],
            response_type=ImageFragment,
        )

    async def fbid_to_cursor(self, thread_id: int | str, media_id: int | str) -> PageInfo:
        return await self.graphql(
            FbIdToCursorQuery(fbid=str(media_id), thread_id=str(thread_id)),
            path=["data", "message_thread", "message_shared_media", "page_info"],
            response_type=PageInfo,
        )

    async def media_query(
        self, thread_id: int | str, cursor: str | None = None
    ) -> SubsequentMediaResponse:
        return await self.graphql(
            SubsequentMediaQuery(thread_id=str(thread_id), cursor_id=cursor),
            path=["data", "message_thread", "mediaResult"],
            response_type=SubsequentMediaResponse,
        )

    async def search(self, query: str, **kwargs) -> SearchEntitiesResponse:
        return await self.graphql(
            SearchEntitiesNamedQuery(search_query=query, **kwargs),
            path=["data", "entities_named"],
            response_type=SearchEntitiesResponse,
        )

    async def get_image_url(
        self,
        message_id: str,
        attachment_id: int | str,
        preview: bool = False,
        max_width: int = 384,
        max_height: int = 480,
    ) -> str | None:
        query = {
            "method": "POST",
            "redirect": "true",
            "access_token": self.state.session.access_token,
            "mid": f"m_{message_id}",
            "aid": str(attachment_id),
        }
        if preview:
            query["preview"] = "1"
            query["max_width"] = max_width
            query["max_height"] = max_height
        headers = {
            "referer": f"fbapp://{self.state.application.client_id}/messenger_thread_photo",
            "x-fb-friendly-name": "image",
        }
        resp = await self.get(
            (self.graph_url / "messaging_get_attachment").with_query(query),
            headers=headers,
            include_auth=False,
            allow_redirects=False,
        )
        # TODO handle errors more properly?
        try:
            return resp.headers["Location"]
        except KeyError:
            return None

    async def get_file_url(
        self, thread_id: str | int, message_id: str, attachment_id: str | int
    ) -> URL | None:
        attachment_id = str(attachment_id)
        msg_id = ThreadMessageID(thread_id=str(thread_id), message_id=message_id)
        try:
            resp = self._file_url_cache[msg_id]
        except KeyError:
            resp = await self.graphql(
                FileAttachmentUrlQuery(thread_msg_id=msg_id),
                path=["data", "message"],
                response_type=FileAttachmentURLResponse,
            )
            if len(resp.blob_attachments) > 1:
                self._file_url_cache[msg_id] = resp
        for attachment in resp.blob_attachments:
            if attachment.attachment_fbid == attachment_id:
                url = URL(resp.blob_attachments[0].url)
                if url.host == "l.facebook.com":
                    url = URL(url.query["u"])
                return url
        return None

    async def get_self(self) -> OwnInfo:
        fields = ",".join(field.name for field in attr.fields(OwnInfo))
        url = (self.graph_url / str(self.state.session.uid)).with_query({"fields": fields})
        async with self.get(url) as resp:
            json_data = await self._handle_response(resp)
        return OwnInfo.deserialize(json_data)

    async def logout(self) -> bool:
        headers = {
            **self._headers,
            "x-fb-friendly-name": "logout",
        }
        req: dict[str, str] = {
            **self._params,
            "fb_api_req_friendly_name": "logout",
            "fb_api_caller_class": "AuthOperations",
        }
        resp = await self.http.post(
            url=self.b_graph_url / "auth" / "expire_session", headers=headers, data=req
        )
        resp.raise_for_status()
        return await resp.text() == "true"

    async def cdn_rmd(self, prev_token: str = "", reason: str = "TIMER_EXPIRED") -> str:
        # reasons: TIMER_EXPIRED, APP_START, APP_RESUME
        headers = {
            **self._headers,
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": "rmd-mapfetcher",
            "x-fb-request-analytics-tags": "rmd",
            "accept-encoding": "x-fb-dz;d=1, gzip, deflate",
        }
        query = {
            "net_iface": self.state.device.net_iface,
            "reason": reason,
        }
        if prev_token:
            query["prev_token"] = prev_token
        resp = await self.http.post(
            url=(self.graph_url / "v3.2" / "cdn_rmd").with_query(query),
            headers=headers,
        )
        await self._decompress_zstd(resp)
        self.log.trace(f"cdn_rmd response: {await resp.text()}")
        json_data = await self._handle_response(resp)
        return json_data["token"]
