"""After a trade: update your Warframe.Market orders by themselves, and show a small card with Undo.

`TradeTracker.feed` gets every game log line (on the log thread). When `TradeReader` sees a trade go
through, the order changes run in the background and `TradeToast` shows what changed, top right,
for 10 seconds. Undo puts the orders back. Turned on and off from the tray ("Update orders after
trades"); it only acts while you are signed in to Warframe.Market.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import settings, theme
from .account import Account
from .items import CatalogItem
from .trades import (OrderChange, Trade, TradeHistory, TradeReader, apply_changes, item_finder, plan_changes,
                     summary, undo)

log = logging.getLogger(__name__)

SETTING = "trade_updates"
SHOW_MS = 10_000
NOTE_MS = 4_000

TOAST_STYLE = """
QWidget#toast { background: #0b1220; border: 1px solid #4fa3ff; border-radius: 8px; }
QLabel { color: #dbe6ff; background: transparent; }
QLabel#title { color: #4fa3ff; font-weight: 600; }
QPushButton { background: #4fa3ff; color: #06101f; border: none; border-radius: 4px; padding: 5px 14px;
              font-weight: 600; }
QPushButton:hover { background: #74b8ff; }
"""


def enabled() -> bool:
    return bool(settings.get(SETTING, True))


class TradeToast(QWidget):
    """The small card: what the trade changed, and an Undo button."""

    def __init__(self, screen: QRect) -> None:
        super().__init__(None)
        self.setObjectName("toast")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.screen_rect = screen
        self.title = QLabel("VoidTrace · orders updated", objectName="title")
        self.text = QLabel()
        self.undo_button = QPushButton("Undo")
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.clicked.connect(self._undo_clicked)
        row = QHBoxLayout()
        row.addWidget(self.text, 1)
        row.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.addWidget(self.title)
        layout.addLayout(row)
        theme.follow(self, TOAST_STYLE)
        self._on_undo: Optional[Callable[[], None]] = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    def show_changes(self, text: str, on_undo: Callable[[], None]) -> None:
        self.title.setText("VoidTrace · orders updated")
        self.text.setText(text)
        self._on_undo = on_undo
        self.undo_button.setEnabled(True)
        self.undo_button.show()
        self._pop(SHOW_MS)

    def show_note(self, text: str) -> None:
        """A short message without Undo (the undo result, or why nothing was changed)."""
        self.text.setText(text)
        self.undo_button.hide()
        self._on_undo = None
        self._pop(NOTE_MS)

    def _undo_clicked(self) -> None:
        if self._on_undo is not None:
            self.undo_button.setEnabled(False)
            self.text.setText(self.text.text() + "\nPutting them back…")
            callback, self._on_undo = self._on_undo, None
            callback()

    def _pop(self, ms: int) -> None:
        self.adjustSize()
        self.move(self.screen_rect.right() - self.width() - 24, self.screen_rect.top() + 24)
        self.show()
        self.raise_()
        self._timer.start(ms)


class TradeTracker(QObject):
    """Reads trades from the log lines and updates the matching orders (see trades.py)."""

    applied = pyqtSignal(object, object)  # trade, the changes that went through
    note = pyqtSignal(str)
    recorded = pyqtSignal(object)  # a TradeRecord, saved to the history

    def __init__(self, account: Account, catalog: Sequence[CatalogItem], toast: TradeToast,
                 is_enabled: Callable[[], bool] = enabled, on_orders_changed: Callable[[], None] = lambda: None,
                 history: Optional[TradeHistory] = None) -> None:
        super().__init__()
        self.account = account
        self.find = item_finder(catalog)
        self.toast = toast
        self.is_enabled = is_enabled
        self.on_orders_changed = on_orders_changed
        self.reader = TradeReader()
        self.history = history or TradeHistory()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VoidTrace-Trades")
        self.applied.connect(self._show)
        self.note.connect(self._note)

    def feed(self, line: str) -> None:
        """Every game log line (log thread)."""
        trade = self.reader.feed(line)
        if trade is None:
            return
        log.info("trade with %s: gave %s; got %s", trade.partner, trade.gave, trade.got)
        self.executor.submit(self.record, trade)  # kept whether or not orders are updated
        if self.is_enabled() and self.account.signed_in:
            self.executor.submit(self.handle, trade)

    def record(self, trade: Trade) -> None:
        self.recorded.emit(self.history.add(trade))

    def handle(self, trade: Trade) -> list[OrderChange]:
        try:
            changes = plan_changes(trade, self.account.my_orders(), self.find)
            done = apply_changes(self.account, changes)
        except Exception as exc:
            log.warning("trade: could not check your orders: %s", exc)
            return []
        if done:
            log.info("trade: updated %d order(s)", len(done))
            self.applied.emit(trade, done)
        return done

    def undo(self, changes: Sequence[OrderChange]) -> None:
        try:
            undo(self.account, changes)
        except Exception as exc:
            log.warning("trade: undo failed: %s", exc)
            self.note.emit(f"Could not put the orders back: {exc}")
            return
        log.info("trade: undid %d order change(s)", len(changes))
        self.note.emit("Orders put back as they were.")

    def _show(self, trade: Trade, changes: list) -> None:
        self.toast.show_changes(summary(trade, changes), lambda: self.executor.submit(self.undo, changes))
        self.on_orders_changed()

    def _note(self, text: str) -> None:
        self.toast.show_note(text)
        self.on_orders_changed()

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
