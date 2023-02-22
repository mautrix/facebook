from __future__ import annotations

import json
import logging
import urllib.request

from yarl import URL


class ProxyHandler:
    current_proxy_url: str | None = None
    log = logging.getLogger("maufbapi.proxy")

    def __init__(self, api_url: str | None) -> None:
        self.api_url = api_url

    def get_proxy_url_from_api(self, reason: str | None = None) -> str | None:
        assert self.api_url is not None

        api_url = str(URL(self.api_url).update_query({"reason": reason} if reason else {}))

        request = urllib.request.Request(api_url, method="GET")
        self.log.debug("Requesting proxy from: %s", api_url)

        try:
            with urllib.request.urlopen(request) as f:
                response = json.loads(f.read().decode())
        except Exception:
            self.log.exception("Failed to retrieve proxy from API")
        else:
            return response["proxy_url"]

        return None

    def update_proxy_url(self, reason: str | None = None) -> bool:
        old_proxy = self.current_proxy_url
        new_proxy = None

        if self.api_url is not None:
            new_proxy = self.get_proxy_url_from_api(reason)
        else:
            new_proxy = urllib.request.getproxies().get("http")

        if old_proxy != new_proxy:
            self.log.debug("Set new proxy URL: %s", new_proxy)
            self.current_proxy_url = new_proxy
            return True

        self.log.debug("Got same proxy URL: %s", new_proxy)
        return False

    def get_proxy_url(self) -> str | None:
        if not self.current_proxy_url:
            self.update_proxy_url()

        return self.current_proxy_url
