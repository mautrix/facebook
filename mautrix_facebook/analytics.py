from __future__ import annotations

from urllib.parse import urlunparse
import logging

import aiohttp

from mautrix.util import background_task

from . import user as u

log = logging.getLogger("mau.web.public.analytics")
http: aiohttp.ClientSession | None = None
analytics_url: str | None = None
analytics_token: str | None = None
analytics_user_id: str | None = None


async def _track(user: u.User, event: str, properties: dict) -> None:
    await http.post(
        analytics_url,
        json={
            "userId": analytics_user_id or user.mxid,
            "event": event,
            "properties": {"bridge": "facebook", **properties},
        },
        auth=aiohttp.BasicAuth(login=analytics_token, encoding="utf-8"),
    )
    log.debug(f"Tracked {event}")


def track(user: u.User, event: str, properties: dict | None = None):
    if analytics_token:
        background_task.create(_track(user, event, properties or {}))


def init(host: str | None, token: str | None, user_id: str | None = None):
    if not host or not token:
        return
    global analytics_url, analytics_token, analytics_user_id, http
    analytics_url = URL.build(scheme="https", host=host, path="/v1/track")
    analytics_token = token
    analytics_user_id = user_id
    http = aiohttp.ClientSession()
