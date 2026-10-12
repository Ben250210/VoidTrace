"""Your Warframe.Market status follows the game: "in game" while Warframe runs, back to what it was
when the game closes. Off by default (tray > "Show me in game on Warframe.Market while playing").

How (checked live on 2026-10-12): Warframe.Market's websocket, wss://ws.warframe.market/socket with
sub-protocol "wfm". Sign in with `@wfm|cmd/auth/signIn {token}`; the server answers with your
current status (`@wfm|event/status/set`). `@wfm|cmd/status/set {status, duration}` changes it and is
acknowledged with `:ok`. A status set with a duration (60-21600 s) falls back by itself when it runs
out, so "in game" is set for an hour at a time and renewed every half hour while the game runs: if
VoidTrace crashes or the PC sleeps, nobody is left thinking you are in game. Each change opens a
short connection and closes it; nothing stays connected.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import settings

log = logging.getLogger(__name__)

SETTING = "online_status"
WS_URL = "wss://ws.warframe.market/socket"
WS_PROTOCOL = "wfm"
INGAME_FOR_S = 3600     # each "in game" lasts this long unless renewed
RENEW_AFTER_S = 1800    # renewed this often while the game runs
CHECK_EVERY_MS = 10_000
BACKOFF_S = (5, 15, 30, 60)
STATUSES = ("ingame", "online", "invisible")
LABEL = {"ingame": "In game", "online": "Online", "invisible": "Invisible", "offline": "Offline"}


class StatusError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.get(SETTING, False))


def _message(route: str, payload: Optional[dict] = None) -> str:
    msg = {"route": route, "id": uuid.uuid4().hex[:11]}
    if payload is not None:
        msg["payload"] = payload
    return json.dumps(msg)


class StatusClient:
    """One status change over a short websocket connection."""

    def __init__(self, connect: Optional[Callable] = None, timeout: float = 10.0) -> None:
        if connect is None:
            import websocket

            def connect():
                return websocket.create_connection(WS_URL, subprotocols=[WS_PROTOCOL], timeout=timeout,
                                                   header=["User-Agent: VoidTrace"])
        self._connect = connect
        self.timeout = timeout

    def set(self, token: str, status: str, duration: Optional[int] = None) -> Optional[str]:
        """Set your status; returns the status you had just before (None if the server did not say)."""
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        ws = self._connect()
        try:
            ws.send(_message("@wfm|cmd/auth/signIn", {"token": token}))
            before = None
            signed_in = False
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline and not (signed_in and before is not None):
                route, payload = self._read(ws)
                if route == "@wfm|cmd/auth/signIn:ok":
                    signed_in = True
                elif route.startswith("@wfm|cmd/auth/signIn"):
                    raise StatusError("Warframe.Market did not accept the session (sign in again).")
                elif route == "@wfm|event/status/set":
                    before = (payload or {}).get("status")
            if not signed_in:
                raise StatusError("Warframe.Market did not answer the sign-in.")
            body = {"status": status}
            if duration is not None:
                body["duration"] = int(duration)
            ws.send(_message("@wfm|cmd/status/set", body))
            while time.monotonic() < deadline:
                route, payload = self._read(ws)
                if route == "@wfm|cmd/status/set:ok":
                    return before
                if route.startswith("@wfm|cmd/status/set"):
                    raise StatusError(f"Warframe.Market refused the status change: {payload}")
            raise StatusError("Warframe.Market did not confirm the status change.")
        finally:
            try:
                ws.close()
            except Exception:
                pass

    @staticmethod
    def _read(ws) -> tuple[str, Optional[dict]]:
        try:
            msg = json.loads(ws.recv())
        except ValueError:
            return "", None
        payload = msg.get("payload")
        return str(msg.get("route", "")), payload if isinstance(payload, dict) else None


class StatusKeeper(QObject):
    """Checks every 10 s whether Warframe is running and sets your status to match."""

    changed = pyqtSignal(str)  # a line for the tray tooltip
    _done = pyqtSignal(object, object, object)  # status set, status before, error

    def __init__(self, token: Callable[[], Optional[str]], is_running: Callable[[], bool],
                 is_enabled: Callable[[], bool] = enabled, client: Optional[StatusClient] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.token = token
        self.is_running = is_running
        self.is_enabled = is_enabled
        self.client = client or StatusClient()
        self.clock = clock
        self.shown: Optional[str] = None      # what VoidTrace last set
        self.restore_to: Optional[str] = None  # what you had before VoidTrace set "in game"
        self.set_at = 0.0
        self.busy = False
        self.failures = 0
        self.retry_at = 0.0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VoidTrace-Status")
        self._done.connect(self._on_done)
        self.timer = QTimer(self, interval=CHECK_EVERY_MS)
        self.timer.timeout.connect(self.tick)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(2_000, self.tick)

    def wanted(self) -> Optional[tuple[str, Optional[int]]]:
        """The change to make now, or None."""
        now = self.clock()
        if self.busy or now < self.retry_at or not self.token():
            return None
        if self.is_enabled() and self.is_running():
            if self.shown != "ingame" or now - self.set_at >= RENEW_AFTER_S:
                return "ingame", INGAME_FOR_S
            return None
        if self.shown == "ingame":
            return self.back_to(), None
        return None

    def back_to(self) -> str:
        return self.restore_to if self.restore_to in ("online", "invisible") else "invisible"

    def tick(self) -> None:
        change = self.wanted()
        if change is None:
            return
        self.busy = True
        token = self.token()
        self.executor.submit(self._work, token, *change)

    def _work(self, token: str, status: str, duration: Optional[int]) -> None:
        try:
            before = self.client.set(token, status, duration)
        except Exception as exc:
            self._done.emit(status, None, exc)
            return
        self._done.emit(status, before, None)

    def _on_done(self, status: str, before, error) -> None:
        self.busy = False
        if error is not None:
            delay = BACKOFF_S[min(self.failures, len(BACKOFF_S) - 1)]
            self.failures += 1
            self.retry_at = self.clock() + delay
            log.warning("could not set Warframe.Market status to %s (retry in %d s): %s", status, delay, error)
            self.changed.emit(f"Warframe.Market status: could not set ({error})")
            return
        self.failures = 0
        if status == "ingame" and self.shown != "ingame":
            self.restore_to = before
        self.shown = status
        self.set_at = self.clock()
        log.info("Warframe.Market status set to %s", status)
        self.changed.emit(f"Warframe.Market status: {LABEL.get(status, status)}")

    def stop(self) -> None:
        """VoidTrace is quitting: put your status back now (a few seconds at most)."""
        self.timer.stop()
        token = self.token()
        if self.shown == "ingame" and token:
            try:
                self.client.set(token, self.back_to())
                self.shown = self.back_to()
                log.info("Warframe.Market status put back to %s", self.shown)
            except Exception as exc:
                log.warning("could not put the Warframe.Market status back (it expires within the hour): %s", exc)
        self.executor.shutdown(wait=False, cancel_futures=True)
