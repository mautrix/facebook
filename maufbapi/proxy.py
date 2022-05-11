import json
import logging
import urllib.request


class ProxyHandler:
    current_proxy_url: str | None = None
    log = logging.getLogger("maufbapi.proxy")

    def _get_proxy_url_from_api(self) -> str | None:
        request = urllib.request.Request(self.api_url, method="GET")

        try:
            with urllib.request.urlopen(request) as f:
                response = json.loads(f.read().decode())
        except Exception:
            self.log.exception("Failed to retrieve proxy from API")
        else:
            self.current_proxy_url = response["proxy_url"]

        return self.current_proxy_url

    def get_proxy_url(self) -> str | None:
        if self.api_url is not None:
            return self._get_proxy_url_from_api(self.api_url)

        try:
            return urllib.request.getproxies()["http"]
        except KeyError:
            return None
