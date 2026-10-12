"""Send VoidTrace notices to your own Discord channel through a webhook: new whispers, price alerts
and finished trades.

You make the webhook in Discord (Server Settings > Integrations > Webhooks > New Webhook > Copy
Webhook URL) and paste it in VoidTrace (sidebar > Discord). Anyone with that link can post to the
channel, so it is kept in Windows Credential Manager, like the Warframe.Market session. Sending runs
on a background thread; a rate limit (HTTP 429) waits and retries once.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import requests

from . import settings, theme

log = logging.getLogger(__name__)

KEYRING_SERVICE = "VoidTrace"
KEYRING_KEY = "discord webhook"
KINDS = {"whispers": "New whispers", "alerts": "Price alerts", "trades": "Finished trades"}
WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")


def is_webhook(url: str) -> bool:
    return bool(WEBHOOK_RE.match((url or "").strip()))


def kind_on(kind: str) -> bool:
    return bool(settings.get(f"discord_{kind}", True))


class DiscordNotifier:
    def __init__(self, keyring: Any = None, post: Callable = requests.post,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if keyring is None:
            import keyring
        self._kr = keyring
        self._post = post
        self._sleep = sleep
        self.last_error = ""
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VoidTrace-Discord")

    def url(self) -> Optional[str]:
        try:
            return self._kr.get_password(KEYRING_SERVICE, KEYRING_KEY)
        except Exception:
            return None

    def set_url(self, url: Optional[str]) -> None:
        """Save the webhook link (None removes it)."""
        if url is None:
            try:
                self._kr.delete_password(KEYRING_SERVICE, KEYRING_KEY)
            except Exception:
                pass  # already gone
            return
        if not is_webhook(url):
            raise ValueError("not a Discord webhook link")
        self._kr.set_password(KEYRING_SERVICE, KEYRING_KEY, url.strip())
        self.last_error = ""

    def send(self, kind: str, title: str, text: str, force: bool = False) -> None:
        """Post a notice, if a webhook is set and this kind is turned on (`force` skips the switch)."""
        url = self.url()
        if url and (force or kind_on(kind)):
            self.executor.submit(self._deliver, url, title, text)

    def _deliver(self, url: str, title: str, text: str) -> None:
        body = {"username": "VoidTrace",
                "embeds": [{"title": title[:256], "description": text[:4000], "color": theme.accent().rgb() & 0xFFFFFF}]}
        for attempt in range(2):
            try:
                resp = self._post(url, json=body, timeout=10)
            except Exception as exc:
                self.last_error = f"Could not reach Discord: {exc}"
                log.warning("discord: %s", self.last_error)
                return
            if resp.status_code == 429 and attempt == 0:
                try:
                    wait = float(resp.json().get("retry_after", 1))
                except Exception:
                    wait = 1.0
                self._sleep(min(wait, 10.0))
                continue
            if resp.status_code in (401, 403, 404):
                self.last_error = "Discord does not know this webhook any more (deleted?). Paste a new link."
            elif resp.status_code >= 400:
                self.last_error = f"Discord refused the message (HTTP {resp.status_code})."
            else:
                self.last_error = ""
            if self.last_error:
                log.warning("discord: %s", self.last_error)
            return

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
