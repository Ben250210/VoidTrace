"""The Market section's Trades tab: every trade VoidTrace saw in the game log, and the plat it made.

Rows come from `TradeHistory` (trades.py), newest first. The top line sums plat earned, spent and
net for today, the last 7 days and all time. The history stays on this PC.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..icons import platinum_icon
from .widgets import BUTTON_STYLE, make_cell, make_table, set_rows
from ..trades import PLATINUM, TradeHistory, TradeItem, TradeRecord, totals

GREEN = QColor("#5ccf8f")
RED = QColor("#f08a6e")


def items_text(items: tuple[TradeItem, ...]) -> str:
    parts = []
    for i in items:
        if i.name.casefold() == PLATINUM:
            continue
        rank = f" (R{i.rank})" if i.rank is not None else ""
        parts.append(f"{i.name}{rank}" + (f" ×{i.quantity}" if i.quantity > 1 else ""))
    return ", ".join(parts) or "—"


def totals_text(label: str, records: list[TradeRecord], since: Optional[datetime]) -> str:
    t = totals(records, since)
    sign = "+" if t.net > 0 else ""
    return f"{label}: {sign}{t.net}p net ({t.earned} in, {t.spent} out, {t.count} trades)"


class TradeHistoryPanel(QWidget):
    COLUMNS = ["Items", "When", "Partner", "Gave / got", "Plat"]
    ITEMS, WHEN, PARTNER, SIDE, PLAT = range(5)

    def __init__(self, history: Optional[TradeHistory] = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        super().__init__()
        self.history = history or TradeHistory()
        self.now = now
        self.records: list[TradeRecord] = []
        self.loaded = False
        theme.follow(self, BUTTON_STYLE)
        self.table = make_table(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(self.ITEMS, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(self.PLAT).setIcon(QIcon(platinum_icon(14)))
        self.today = QLabel()
        self.week = QLabel(objectName="muted")
        self.all_time = QLabel(objectName="muted")
        self.reload = QPushButton("Refresh")
        self.reload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload.clicked.connect(self.refresh)
        top = QHBoxLayout()
        sums = QVBoxLayout()
        for label in (self.today, self.week, self.all_time):
            sums.addWidget(label)
        top.addLayout(sums, 1)
        top.addWidget(self.reload, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("Trades are read from the game log while VoidTrace runs, and kept on this PC only.",
                                objectName="muted"))

    def refresh(self) -> None:
        self.records = self.history.load()
        self.loaded = True
        self._render()

    def add(self, record: TradeRecord) -> None:
        """A trade just happened (from TradeTracker.recorded)."""
        if not self.loaded:
            self.refresh()  # the file already holds this trade
            return
        self.records.append(record)
        self._render()

    def _render(self) -> None:
        now = self.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.today.setText(totals_text("Today", self.records, midnight))
        self.week.setText(totals_text("Last 7 days", self.records, now - timedelta(days=7)))
        self.all_time.setText(totals_text("All time", self.records, None))
        rows = list(reversed(self.records))
        self.table.clearSpans()
        if not rows:
            set_rows(self.table, 1)
            self.table.setSpan(0, 0, 1, len(self.COLUMNS))
            self.table.setItem(0, 0, make_cell("No trades yet. They show up here after you trade in game.",
                                               color=QColor(143, 163, 194)))
            return
        set_rows(self.table, len(rows))
        for row, r in enumerate(rows):
            gave, got = items_text(r.trade.gave), items_text(r.trade.got)
            if got == "—":
                items, side = gave, "Sold"
            elif gave == "—":
                items, side = got, "Bought"
            else:
                items, side = f"{gave}  ⇄  {got}", "Swapped"
            when = r.time.strftime("%H:%M") if r.time >= midnight else r.time.strftime("%d %b %H:%M")
            plat = r.plat
            self.table.setItem(row, self.ITEMS, make_cell(items))
            self.table.setItem(row, self.WHEN, make_cell(when))
            self.table.setItem(row, self.PARTNER, make_cell(r.trade.partner))
            self.table.setItem(row, self.SIDE, make_cell(side))
            self.table.setItem(row, self.PLAT, make_cell(f"{plat:+d}" if plat else "0", right=True,
                                                     color=GREEN if plat > 0 else RED if plat < 0 else None))
