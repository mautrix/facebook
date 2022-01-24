import logging
import os

from yarl import URL
import aiohttp

from ..user import User

log = logging.getLogger("mau.web.public.analytics")
http: aiohttp.ClientSession
host: str = "api.segment.io"

try:
    segment_key = os.environ["SEGMENT_API_KEY"]
except KeyError:
    segment_key = None
else:
    http = aiohttp.ClientSession()


def track(user: User, event: str, properties: dict) -> None:
    if segment_key:
        properties["bridge"] = "facebook"
        await http.post(
            URL.build(scheme="https", host=host, path="/v1/track"),
            json={
                "userId": user.mx_id,
                "event": event,
                "properties": properties,
            },
            auth=aiohttp.BasicAuth(login=segment_key, encoding="utf-8"),
        )
        log.debug(f"Tracked {event}")
