"""Price alerts: "tell me when someone sells Volt Prime Set for 55p or less".

Alerts live in settings.json (`alerts`). `AlertWatcher` checks them every minute on a worker thread,
through the same rate-limited client as the Market section, and emits `found` once per seller and
price. The tray shows it; clicking the message copies the whisper. Only public data is read.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import settings
from .sellers import SellerOrder, SellerService

log = logging.getLogger(__name__)

SETTING = "alerts"
CHECK_EVERY_MS = 60_000
GAP_S = 0.4  # between alerts, to stay well under Warframe.Market's rate limit


@dataclass(frozen=True)
class Alert:
    slug: str
    name: str
    max_price: int
    rank: Optional[int] = None
    ranked: bool = False  # the item has ranks (the whisper then names the rank)

    @property
    def label(self) -> str:
        return f"{self.name} (rank {self.rank})" if self.rank is not None else self.name


def load_alerts() -> list[Alert]:
    alerts = []
    for d in settings.get(SETTING, []) or []:
        try:
            alerts.append(Alert(str(d["slug"]), str(d["name"]), int(d["max_price"]), d.get("rank"),
                                bool(d.get("ranked", False))))
        except (KeyError, TypeError, ValueError):
            continue
    return alerts


def save_alerts(alerts: Sequence[Alert]) -> None:
    settings.put(SETTING, [asdict(a) for a in alerts])


def add_alert(alert: Alert) -> list[Alert]:
    """Add, replacing any alert for the same item and rank."""
    alerts = [a for a in load_alerts() if (a.slug, a.rank) != (alert.slug, alert.rank)] + [alert]
    save_alerts(alerts)
    return alerts


def remove_alert(alert: Alert) -> list[Alert]:
    alerts = [a for a in load_alerts() if (a.slug, a.rank) != (alert.slug, alert.rank)]
    save_alerts(alerts)
    return alerts


def check(alert: Alert, sellers: Sequence[SellerOrder], me: Optional[str] = None) -> Optional[SellerOrder]:
    """The cheapest seller at or under the alert price. Your own listing and prices flagged as troll
    listings (far below everyone else) are skipped."""
    mine = (me or "").casefold()
    hits = [s for s in sellers if s.platinum <= alert.max_price and not s.suspicious
            and (alert.rank is None or s.rank == alert.rank) and (not mine or s.ingame_name.casefold() != mine)]
    return min(hits, key=lambda s: (s.platinum, -s.reputation), default=None)


class AlertWatcher(QObject):
    found = pyqtSignal(object, object)  # Alert, SellerOrder

    def __init__(self, sellers: SellerService, me: Callable[[], Optional[str]] = lambda: None,
                 load: Callable[[], list[Alert]] = load_alerts, sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__()
        self.sellers = sellers
        self.me = me
        self.load = load
        self.sleep = sleep
        self.seen: set[tuple] = set()
        self._busy = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VoidTrace-Alerts")
        self.timer = QTimer(self, interval=CHECK_EVERY_MS)
        self.timer.timeout.connect(self.check_now)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(5_000, self.check_now)

    def check_now(self) -> None:
        if self.load():
            self.executor.submit(self.run)

    def run(self) -> None:
        if not self._busy.acquire(blocking=False):
            return  # the last round is still going
        try:
            for i, alert in enumerate(self.load()):
                if i:
                    self.sleep(GAP_S)
                try:
                    seller = check(alert, self.sellers.sellers(alert.slug, alert.rank), self.me())
                except Exception as exc:
                    log.warning("price alert check failed for %s: %s", alert.slug, exc)
                    continue
                if seller is None:
                    continue
                key = (alert.slug, alert.rank, seller.ingame_name, seller.platinum)
                if key in self.seen:
                    continue
                self.seen.add(key)
                log.info("price alert: %s at %dp", alert.label, seller.platinum)
                self.found.emit(alert, seller)
        finally:
            self._busy.release()

    def shutdown(self) -> None:
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
