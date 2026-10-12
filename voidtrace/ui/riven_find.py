"""The Rivens section's Find page: riven auctions for a weapon, filtered by stats, curse and rolls;
click a row to copy the whisper. Also the auction table shared with the Price check page."""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..icons import platinum_icon
from ..rivens import (
    RivenAuction,
    RivenCatalog,
    RivenWeapon,
    CLEAN,
    ROLLED,
    grade_from_percent,
    riven_grade,
    riven_whisper,
)
from .widgets import make_cell, make_table
from .widgets import BUFF_COLOR, CURSE_COLOR, STATUS_COLOR, STATUS_LABEL


if TYPE_CHECKING:
    from .rivens import RivenSection

log = logging.getLogger(__name__)


CLEAN_COLOR = QColor(124, 201, 156)  # never-rolled rivens: worth calling out to buyers


# Rank matters: sellers list stat values at the riven's current rank (a rank-0 riven shows about a
# ninth of its maxed values).
AUCTION_COLUMNS = ["Riven", "Buffs", "Curse", "MR", "Rank", "Rolls", "Grade", "Price", "Seller"]


PRICE_COLUMN = AUCTION_COLUMNS.index("Price")


MAX_BUFFS = 3


ANY_CURSE, NO_CURSE, HAS_CURSE = None, "none", "has"


def disposition_dots(disposition: float) -> str:
    """Riven strength as the game shows it: 1-5 filled dots."""
    filled = 1 + (disposition >= 0.7) + (disposition >= 0.9) + (disposition > 1.1) + (disposition > 1.3)
    return "●" * filled + "○" * (5 - filled)


def weapon_title(weapon: RivenWeapon) -> str:
    return (f'{html.escape(weapon.name)} <span style="color:#8fa3c2; font-size:13px; font-weight:400">'
            f'{disposition_dots(weapon.disposition)} {weapon.disposition:.2f}</span>')


def auction_table() -> QTableWidget:
    table = make_table(AUCTION_COLUMNS)
    table.setWordWrap(True)
    table.horizontalHeaderItem(PRICE_COLUMN).setIcon(QIcon(platinum_icon(14)))
    header = table.horizontalHeader()
    # Names and stat lines must not elide or wrap ("Ru... Igni..."); the seller column takes the slack.
    for c in range(len(AUCTION_COLUMNS)):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(len(AUCTION_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
    return table


def grade_color(value: Optional[int]) -> Optional[QColor]:
    """Red (0) through amber to green (100), like the bars other riven tools draw."""
    if value is None:
        return None
    t = value / 100
    return QColor(int(240 - 116 * t), int(110 + 91 * t), int(90 + 66 * t))


def fill_auction_table(table: QTableWidget, catalog: RivenCatalog, auctions: list[RivenAuction]) -> None:
    table.setRowCount(0)
    table.setRowCount(len(auctions))
    plat = platinum_icon(14)
    for r, a in enumerate(auctions):
        buffs = "\n".join(catalog.format_attr(x) for x in a.attrs if x.positive)
        curse = "\n".join(catalog.format_attr(x) for x in a.attrs if not x.positive) or "none"
        price = "bids only" if a.price is None else (str(a.price) if a.buyout is not None else f"{a.price} (bid)")
        g = riven_grade(catalog, catalog.by_weapon.get(a.weapon), a.attrs, a.rank)
        cells = [
            make_cell(catalog.riven_title(a)),
            make_cell(buffs, color=BUFF_COLOR),
            make_cell(curse, color=CURSE_COLOR if a.negative else QColor(143, 163, 194)),
            make_cell(str(a.mr), right=True),
            make_cell(str(a.rank), right=True),
            make_cell("0 \u00b7 clean" if a.rolls == 0 else str(a.rolls), right=True,
                  color=CLEAN_COLOR if a.rolls == 0 else None),
            make_cell("" if g is None else grade_from_percent(g), right=True, color=grade_color(g)),
            make_cell(price, right=True, icon=plat),
            make_cell(f"{a.seller} · {STATUS_LABEL.get(a.status, a.status)}", color=STATUS_COLOR.get(a.status)),
        ]
        for c, cell in enumerate(cells):
            table.setItem(r, c, cell)
    table.resizeRowsToContents()


def stat_combo(stats, first: list[tuple[str, Optional[str]]]) -> QComboBox:
    box = QComboBox()
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    for label, data in first:
        box.addItem(label, data)
    for s in stats:
        box.addItem(s.name, s.slug)
    return box


def refill_combo(box: QComboBox, stats, first: list[tuple[str, Optional[str]]]) -> None:
    keep = box.currentData()
    box.blockSignals(True)
    box.clear()
    for label, data in first:
        box.addItem(label, data)
    for s in stats:
        box.addItem(s.name, s.slug)
    index = box.findData(keep)
    box.setCurrentIndex(index if index >= 0 else 0)
    box.blockSignals(False)


class FindPanel(QWidget):
    def __init__(self, window: "RivenSection") -> None:
        super().__init__()
        self.window_ = window
        self.auctions: list[RivenAuction] = []
        self._request = 0
        self.buffs = [stat_combo([], [("Any stat", None)]) for _ in range(MAX_BUFFS)]
        self.curse = stat_combo([], self.curse_choices())
        self.buyout_only = QCheckBox("Buyout only")
        self.offline = QCheckBox("Include offline sellers")
        self.rolls = QComboBox()
        for label, value in (("Any rolls", None), ("Clean (0 rolls)", CLEAN), ("Rolled (1+)", ROLLED)):
            self.rolls.addItem(label, value)
        self.rolls.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.table = auction_table()
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status = QLabel(objectName="muted")

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Wanted:"))
        for box in self.buffs:
            filters.addWidget(box)
        filters.addWidget(QLabel("Curse:"))
        filters.addWidget(self.curse)
        filters.addStretch(1)
        toggles = QHBoxLayout()
        toggles.addWidget(self.buyout_only)
        toggles.addWidget(self.offline)
        toggles.addWidget(self.rolls)
        toggles.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(filters)
        layout.addLayout(toggles)
        layout.addWidget(QLabel("Click a riven to copy the whisper, then paste it in game chat.", objectName="muted"))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

        for box in (*self.buffs, self.curse):
            box.currentIndexChanged.connect(lambda _: self.refresh())
        self.rolls.currentIndexChanged.connect(lambda _: self.refresh())
        for check in (self.buyout_only, self.offline):
            check.toggled.connect(lambda _: self.refresh())
        self.table.cellClicked.connect(lambda row, _col: self.copy_row(row))

    @staticmethod
    def curse_choices() -> list[tuple[str, Optional[str]]]:
        return [("Any curse", ANY_CURSE), ("No curse", NO_CURSE), ("Has a curse", HAS_CURSE)]

    def set_weapon(self, weapon: RivenWeapon) -> None:
        stats = self.window_.catalog.stats_for(weapon)
        for box in self.buffs:
            refill_combo(box, [s for s in stats if not s.negative_only], [("Any stat", None)])
        refill_combo(self.curse, [s for s in stats if not s.positive_only], self.curse_choices())
        self.refresh()

    def wanted(self) -> list[str]:
        picked = [box.currentData() for box in self.buffs if box.currentData()]
        return list(dict.fromkeys(picked))  # the same stat twice counts once

    def refresh(self) -> None:
        weapon = self.window_.weapon
        if weapon is None:
            return
        self._request += 1
        request = self._request
        statuses = ("ingame", "online", "offline") if self.offline.isChecked() else ("ingame", "online")
        wanted, curse, buyout = self.wanted(), self.curse.currentData(), self.buyout_only.isChecked()
        rolls = self.rolls.currentData()
        self.status.setText("Loading rivens…")
        self.window_.run_async(
            lambda: self.window_.service.search(weapon.slug, wanted, curse, buyout, statuses, rolls=rolls),
            lambda rows: self._show(request, rows),
            lambda exc: self._failed(request, exc))

    def _failed(self, request: int, exc: Exception) -> None:
        if request == self._request:
            self.status.setText(f"Could not load rivens: {exc}")

    def _show(self, request: int, rows: list[RivenAuction]) -> None:
        if request != self._request:
            return  # an older search finished after a newer one started
        self.auctions = rows
        fill_auction_table(self.table, self.window_.catalog, rows)
        if rows:
            self.status.setText(f"{len(rows)} rivens, sellers in game first, then cheapest.")
        else:
            self.status.setText("No rivens listed with these stats right now.")

    def whisper_for(self, row: int) -> str:
        a = self.auctions[row]
        return riven_whisper(a, self.window_.catalog.riven_title(a))

    def copy_row(self, row: int) -> None:
        if not (0 <= row < len(self.auctions)):
            return
        QApplication.clipboard().setText(self.whisper_for(row))
        self.window_.show_toast(f"Copied whisper to {self.auctions[row].seller}. Paste it in game chat.")
