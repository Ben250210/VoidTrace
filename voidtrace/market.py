"""Shared Warframe.Market API access (public, read-only, no auth).

Requests are spaced to stay under the site's ~3 requests/second guidance.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import requests

API_BASE = "https://api.warframe.market/v2"
HEADERS = {
    "Accept": "application/json",
    "Language": "en",
    "Platform": "pc",
    "User-Agent": "VoidTrace/0.1 (read-only Warframe companion)",
}
MIN_INTERVAL = 0.35  # seconds between requests


class MarketClient:
    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 10.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_request = 0.0

    def get(self, path: str) -> Any:
        """GET {API_BASE}{path} and return the `data` field of the JSON envelope."""
        with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
        resp = self.session.get(API_BASE + path, headers=HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["data"]
