import asyncio
import logging

from yarl import URL
import aiohttp

from ..user import User

log = logging.getLogger("mau.web.public.analytics")
http: aiohttp.ClientSession = aiohttp.ClientSession()
host: str = "api.segment.io"
segment_key: str | None = None


async def _track(user: User, event: str, properties: dict) -> None:
    if segment_key:
        properties["bridge"] = "facebook"
        await http.post(
            URL.build(scheme="https", host=host, path="/v1/track"),
            json={
                "userId": user.mxid,
                "event": event,
                "properties": properties,
            },
            auth=aiohttp.BasicAuth(login=segment_key, encoding="utf-8"),
        )
        log.debug(f"Tracked {event}")


def track(*args, **kwargs):
    asyncio.create_task(_track(*args, **kwargs))


def init(segment_key):
    global segment_key
    segment_key = segment_key
