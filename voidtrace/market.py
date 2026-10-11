"""Shared Warframe.Market API access.

Public data needs no account. Signed-in calls (your own orders) go through `request()` with a
token from voidtrace.account. Every request is spaced to stay under the site's ~3 requests/second
guidance.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import requests

API_BASE = "https://api.warframe.market/v2"
API_BASE_V1 = "https://api.warframe.market/v1"
HEADERS = {
    "Accept": "application/json",
    "Language": "en",
    "Platform": "pc",
    "User-Agent": "VoidTrace/0.2 (Warframe companion)",
}
MIN_INTERVAL = 0.35  # seconds between requests


class MarketClient:
    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 10.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_request = 0.0

    def get(self, path: str) -> Any:
        """GET {API_BASE}{path} (v2) and return the `data` field of the JSON envelope."""
        return self._get(API_BASE + path)["data"]

    def get_v1(self, path: str) -> Any:
        """GET a legacy v1 endpoint and return its `payload`. Used for closed-trade statistics,
        which v2 does not expose."""
        return self._get(API_BASE_V1 + path)["payload"]

    def _get(self, url: str) -> Any:
        resp = self.request("GET", url)
        resp.raise_for_status()
        return resp.json()

    def request(self, method: str, url: str, headers: Optional[dict] = None, json: Any = None) -> requests.Response:
        """Rate-limited request; the caller checks the status. `url` may be a v1/v2 path or a full URL."""
        if url.startswith("/"):
            url = API_BASE + url
        with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
        return self.session.request(method, url, headers={**HEADERS, **(headers or {})}, json=json,
                                    timeout=self.timeout)
