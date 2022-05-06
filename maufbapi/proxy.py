from typing import Optional
import urllib.request


def get_proxy_url() -> Optional[str]:
    try:
        return urllib.request.getproxies()["http"]
    except KeyError:
        return None
