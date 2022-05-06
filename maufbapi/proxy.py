import json
import logging
import urllib.request

log = logging.getLogger("maufbapi.proxy")
cached_url: str | None = None


def _get_proxy_url_from_api(api_url: str) -> str | None:
    global cached_url

    request = urllib.request.Request(api_url, method="GET")

    try:
        with urllib.request.urlopen(request) as f:
            response = json.loads(f.read().decode())
    except Exception:
        log.exception("Failed to retrieve proxy from API")
    else:
        cached_url = response["proxy_url"]

    return cached_url


def get_proxy_url(api_url: str | None) -> str | None:
    if api_url is not None:
        return _get_proxy_url_from_api(api_url)

    try:
        return urllib.request.getproxies()["http"]
    except KeyError:
        return None
