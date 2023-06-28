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

from typing import AsyncIterable
from uuid import uuid4
import asyncio
import time

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
    MinimalThreadListResponse,
    MoreMessagesQuery,
    MoreThreadsQuery,
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
    UsersQuery,
    UsersQueryResponse,
)
from ..types.graphql import PageInfo, Participant, Thread, ThreadMessageID
from .base import BaseAndroidAPI
from .errors import RateLimitExceeded, ResponseError
from .login import LoginAPI
from .post_login import PostLoginAPI
from .upload import UploadAPI


class AndroidAPI(LoginAPI, PostLoginAPI, UploadAPI, BaseAndroidAPI):
    _file_url_cache: dict[ThreadMessageID, FileAttachmentURLResponse]
    _page_size = 20

    async def fetch_thread_list(self, **kwargs) -> ThreadListResponse:
        return await self.graphql(
            ThreadListQuery(**kwargs),
            response_type=ThreadListResponse,
            path=["data", "viewer", "message_threads"],
        )

    async def fetch_more_threads(self, after_time_ms: int, **kwargs) -> MinimalThreadListResponse:
        return await self.graphql(
            MoreThreadsQuery(after_time_ms=str(after_time_ms), **kwargs),
            path=["data", "viewer", "message_threads"],
            response_type=MinimalThreadListResponse,
        )

    async def iter_thread_list(
        self,
        initial_resp: ThreadListResponse | None = None,
        local_limit: int | None = None,
    ) -> AsyncIterable[Thread]:
        if not initial_resp:
            initial_resp = await self.fetch_thread_list(thread_count=self._page_size)
        after_ts = int(time.time() * 1000)
        thread_counter = 0
        for thread in initial_resp.nodes:
            yield thread
            after_ts = min(after_ts, thread.updated_timestamp)
            thread_counter += 1
            if local_limit and thread_counter >= local_limit:
                return

        local_limit = local_limit - thread_counter if local_limit else None
        async for thread in self.iter_thread_list_from(after_ts, local_limit=local_limit):
            yield thread

    async def iter_thread_list_from(
        self,
        timestamp: int,
        local_limit: int | None = None,
        rate_limit_exceeded_backoff: float = 60.0,
    ) -> AsyncIterable[Thread]:
        if local_limit and local_limit <= 0:
            return
        thread_counter = 0
        page_size = self._page_size
        while True:
            self.log.debug(f"Fetching {page_size} more threads from before {timestamp}")

            try:
                resp = await self.fetch_more_threads(timestamp - 1, thread_count=page_size)
            except RateLimitExceeded:
                self.log.warning(
                    "Fetching more threads failed due to rate limit. Waiting for "
                    f"{rate_limit_exceeded_backoff} seconds before resuming."
                )
                await asyncio.sleep(rate_limit_exceeded_backoff)
                continue
            except ResponseError as e:
                self.log.warning(
                    f"Failed to fetch batch of {page_size} after {timestamp - 1}. Error: {e}"
                )
                await asyncio.sleep(10)

                if page_size == 1:
                    # We are already going one at a time, so we know that the next thread is the
                    # broken one. Find a point where we can start fetching again. Note that this
                    # may cause some threads to be missed, but since failures of this sort are
                    # probably rare, that's an acceptable tradeoff.
                    day_ms = 24 * 60 * 60 * 1000
                    backoff_days = 1
                    possibly_good = timestamp - (backoff_days * day_ms)
                    while possibly_good > 1262304000000:  # 2010-01-01
                        self.log.debug(f"Checking if timestamp {possibly_good} works")
                        try:
                            resp = await self.fetch_more_threads(possibly_good, thread_count=1)
                            self.log.debug(f"Timestamp {possibly_good} worked.")

                            # Reset the page size because if we made it here, we know that the
                            # fetch worked properly, so we can go back to fetching full pages.
                            self.log.debug(f"Resetting page size to {self._page_size}")
                            page_size = self._page_size
                            break
                        except ResponseError as e:
                            if backoff_days < 16:
                                backoff_days *= 2
                            self.log.debug(
                                f"Timestamp {possibly_good} still doesn't work: {e}. "
                                f"Will retry with {possibly_good - backoff_days * day_ms}"
                            )
                            possibly_good -= backoff_days * day_ms
                            await asyncio.sleep(10)
                    else:  # nobreak
                        self.log.info(
                            "No good timestamp before the beginning of Messenger's existence"
                        )
                        return
                else:
                    page_size = 1  # Go one at a time until we find the thread that is broken.
                    continue

            for thread in resp.nodes:
                yield thread
                timestamp = min(timestamp, thread.updated_timestamp)
                thread_counter += 1
                if local_limit and thread_counter >= local_limit:
                    return

            if len(resp.nodes) < page_size:
                return

    async def fetch_thread_info(self, *thread_ids: str | int, **kwargs) -> list[Thread]:
        resp = await self.graphql(
            ThreadQuery(thread_ids=[str(i) for i in thread_ids], **kwargs),
            path=["data"],
            response_type=ThreadQueryResponse,
        )
        return resp.message_threads

    async def fetch_user_info(self, *user_ids: str | int, **kwargs) -> list[Participant]:
        resp = await self.graphql(
            UsersQuery(user_fbids=[str(i) for i in user_ids], **kwargs),
            path=["data"],
            response_type=UsersQueryResponse,
        )
        return resp.messaging_actors

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

    async def delete_for_me(self, message_id: str) -> None:
        headers = {
            **self._headers,
            "x-fb-friendly-name": "deleteMessages",
            "x-fb-request-analytics-tags": "unknown",
        }
        params = {
            **self._params,
            "ids": f"m_{message_id}",
            "format": "json",
            "method": "DELETE",
            "fb_api_req_friendly_name": "deleteMessages",
            "fb_api_caller_class": "MultiCacheThreadsQueue",
        }
        resp = await self.http_post(
            url=self.graph_url,
            data=params,
            headers=headers,
        )
        self.log.debug("Response to delete for me: HTTP %d / %s", resp.status, await resp.text())

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
        resp = await self.http_get(
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
        resp = await self.http_get(url)
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
        resp = await self.http_post(
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
        resp = await self.http_post(
            url=(self.graph_url / "v3.2" / "cdn_rmd").with_query(query),
            headers=headers,
        )
        await self._decompress_zstd(resp)
        self.log.trace(f"cdn_rmd response: {await resp.text()}")
        json_data = await self._handle_response(resp)
        return json_data["token"]
