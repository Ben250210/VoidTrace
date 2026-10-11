"""Riven window: find riven auctions for a weapon, and price-check a riven you own.

A separate window from the market window (rivens are auctions, not orders), with the same look:
  * Find rivens  - auctions for a weapon, filtered by wanted stats and curse; click to copy the whisper
  * Price check  - type in your riven's stats; the estimate comes from comparable listings
"""

from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .account import Account
from .icons import platinum_icon
from .market_window import STYLE, search_catalog
from .rivens import (
    POLARITIES,
    PriceCheck,
    RivenAttr,
    RivenAuction,
    RivenCatalog,
    RivenDraft,
    RivenService,
    RivenWeapon,
    price_check,
    riven_whisper,
)
from .trade_panel import BUTTON_STYLE, Stepper, _cell, _table

log = logging.getLogger(__name__)

BUFF_COLOR = QColor(124, 201, 156)
CURSE_COLOR = QColor(240, 110, 90)
STATUS_LABEL = {"ingame": "In game", "online": "Online", "offline": "Offline"}
STATUS_COLOR = {"ingame": QColor(124, 201, 156), "online": QColor(143, 163, 255), "offline": QColor(155, 160, 181)}
# Rank matters: sellers list stat values at the riven's current rank (a rank-0 riven shows about a
# ninth of its maxed values).
AUCTION_COLUMNS = ["Riven", "Buffs", "Curse", "MR", "Rank", "Rolls", "Price", "Seller"]
PRICE_COLUMN = AUCTION_COLUMNS.index("Price")
MAX_BUFFS = 3

ANY_CURSE, NO_CURSE, HAS_CURSE = None, "none", "has"


def disposition_dots(disposition: float) -> str:
    """Riven strength as the game shows it: 1-5 filled dots."""
    filled = 1 + (disposition >= 0.7) + (disposition >= 0.9) + (disposition > 1.1) + (disposition > 1.3)
    return "●" * filled + "○" * (5 - filled)


def weapon_title(weapon: RivenWeapon) -> str:
    return (f'{html.escape(weapon.name)} <span style="color:#9ba0b5; font-size:13px; font-weight:400">'
            f'{disposition_dots(weapon.disposition)} {weapon.disposition:.2f}</span>')


def auction_table() -> QTableWidget:
    table = _table(AUCTION_COLUMNS)
    table.setWordWrap(True)
    table.horizontalHeaderItem(PRICE_COLUMN).setIcon(QIcon(platinum_icon(14)))
    header = table.horizontalHeader()
    # Names and stat lines must not elide or wrap ("Ru... Igni..."); the seller column takes the slack.
    for c in range(len(AUCTION_COLUMNS)):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(len(AUCTION_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
    return table


def fill_auction_table(table: QTableWidget, catalog: RivenCatalog, auctions: list[RivenAuction]) -> None:
    table.setRowCount(0)
    table.setRowCount(len(auctions))
    plat = platinum_icon(14)
    for r, a in enumerate(auctions):
        buffs = "\n".join(catalog.format_attr(x) for x in a.attrs if x.positive)
        curse = "\n".join(catalog.format_attr(x) for x in a.attrs if not x.positive) or "none"
        price = "bids only" if a.price is None else (str(a.price) if a.buyout is not None else f"{a.price} (bid)")
        cells = [
            _cell(catalog.riven_title(a)),
            _cell(buffs, color=BUFF_COLOR),
            _cell(curse, color=CURSE_COLOR if a.negative else QColor(155, 160, 181)),
            _cell(str(a.mr), right=True),
            _cell(str(a.rank), right=True),
            _cell(str(a.rolls), right=True),
            _cell(price, right=True, icon=plat),
            _cell(f"{a.seller} · {STATUS_LABEL.get(a.status, a.status)}", color=STATUS_COLOR.get(a.status)),
        ]
        for c, cell in enumerate(cells):
            table.setItem(r, c, cell)
    table.resizeRowsToContents()


def _stat_combo(stats, first: list[tuple[str, Optional[str]]]) -> QComboBox:
    box = QComboBox()
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    for label, data in first:
        box.addItem(label, data)
    for s in stats:
        box.addItem(s.name, s.slug)
    return box


def _refill(box: QComboBox, stats, first: list[tuple[str, Optional[str]]]) -> None:
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
    def __init__(self, window: "RivenWindow") -> None:
        super().__init__()
        self.window_ = window
        self.auctions: list[RivenAuction] = []
        self._request = 0
        self.buffs = [_stat_combo([], [("Any stat", None)]) for _ in range(MAX_BUFFS)]
        self.curse = _stat_combo([], self.curse_choices())
        self.buyout_only = QCheckBox("Buyout only")
        self.offline = QCheckBox("Include offline sellers")
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
        for check in (self.buyout_only, self.offline):
            check.toggled.connect(lambda _: self.refresh())
        self.table.cellClicked.connect(lambda row, _col: self.copy_row(row))

    @staticmethod
    def curse_choices() -> list[tuple[str, Optional[str]]]:
        return [("Any curse", ANY_CURSE), ("No curse", NO_CURSE), ("Has a curse", HAS_CURSE)]

    def set_weapon(self, weapon: RivenWeapon) -> None:
        stats = self.window_.catalog.stats_for(weapon)
        for box in self.buffs:
            _refill(box, [s for s in stats if not s.negative_only], [("Any stat", None)])
        _refill(self.curse, [s for s in stats if not s.positive_only], self.curse_choices())
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
        self.status.setText("Loading rivens…")
        self.window_.run_async(
            lambda: self.window_.service.search(weapon.slug, wanted, curse, buyout, statuses),
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


class StatRow:
    """One stat line of the price-check form: stat picker + value."""

    def __init__(self, curse: bool, catalog: RivenCatalog) -> None:
        self.curse = curse
        self.catalog = catalog
        self.box = _stat_combo([], [("No curse" if curse else "—", None)])
        self.value = QDoubleSpinBox(minimum=-1000.0, maximum=1000.0, decimals=2)
        self.value.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.value.setMinimumWidth(80)

    def attr(self) -> Optional[RivenAttr]:
        slug = self.box.currentData()
        if not slug:
            return None
        value = self.value.value()
        stat = self.catalog.by_stat.get(slug)
        flipped = stat is not None and stat.positive_is_negative  # recoil: the curse is a plus
        if self.curse and value > 0 and not flipped:
            value = -value  # curses can be typed as plain numbers; most are a minus
        return RivenAttr(slug, value, not self.curse)


class PricePanel(QWidget):
    sell_requested = pyqtSignal(object, object)  # (RivenDraft, PriceCheck)

    def __init__(self, window: "RivenWindow") -> None:
        super().__init__()
        self.window_ = window
        self.check: Optional[PriceCheck] = None
        self._request = 0
        self.name = QLineEdit(placeholderText="Riven name, e.g. Crita-acritis")
        self.rows = [StatRow(False, window.catalog) for _ in range(MAX_BUFFS)] + [StatRow(True, window.catalog)]
        self.mr = Stepper(8, 16, 8)
        self.rolls = Stepper(0, 9999, 0)
        self.rank = Stepper(0, 8, 0)
        self.polarity = QComboBox()
        for p in POLARITIES:
            self.polarity.addItem(p.title(), p)
        self.check_button = QPushButton("Check price", objectName="primary")
        self.sell_button = QPushButton("Sell this riven…")
        self.sell_button.setEnabled(False)
        self.result = QLabel(objectName="title")
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.detail = QLabel(objectName="muted")
        self.detail.setWordWrap(True)
        self.table = auction_table()

        form = QGridLayout()
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(self.name, 0, 1, 1, 3)
        for i, row in enumerate(self.rows):
            form.addWidget(QLabel("Curse" if row.curse else f"Buff {i + 1}"), 1 + i, 0)
            form.addWidget(row.box, 1 + i, 1, 1, 2)
            form.addWidget(row.value, 1 + i, 3)
        extras = QHBoxLayout()
        for label, widget in (("MR", self.mr), ("Rolls", self.rolls), ("Rank", self.rank), ("Polarity", self.polarity)):
            extras.addWidget(QLabel(label))
            extras.addWidget(widget)
        extras.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.sell_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(form)
        layout.addLayout(extras)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(self.detail)
        layout.addWidget(self.table, 1)

        self.check_button.clicked.connect(self.run_check)
        self.sell_button.clicked.connect(lambda: self.sell_requested.emit(self.draft(), self.check))

    def set_weapon(self, weapon: RivenWeapon) -> None:
        stats = self.window_.catalog.stats_for(weapon)
        for row in self.rows:
            allowed = [s for s in stats if not (s.positive_only if row.curse else s.negative_only)]
            _refill(row.box, allowed, [("No curse" if row.curse else "—", None)])
        self.check = None
        self.result.setText("")
        self.detail.setText("Enter your riven's stats, then Check price.")
        self.table.setRowCount(0)
        self.sell_button.setEnabled(False)

    def draft(self) -> RivenDraft:
        weapon = self.window_.weapon
        attrs = tuple(a for a in (row.attr() for row in self.rows) if a is not None)
        return RivenDraft(weapon=weapon.slug if weapon else "", name=self.name.text().strip(), attrs=attrs,
                          mr=self.mr.value(), rolls=self.rolls.value(), rank=self.rank.value(),
                          polarity=self.polarity.currentData())

    def load_draft(self, draft: RivenDraft) -> None:
        """Fill the form, e.g. from a riven read off the screen."""
        self.name.setText(draft.name)
        buffs = [a for a in draft.attrs if a.positive][:MAX_BUFFS]
        curse = [a for a in draft.attrs if not a.positive][:1]
        for row, attr in zip(self.rows, [*buffs, *[None] * (MAX_BUFFS - len(buffs)), *(curse or [None])]):
            index = row.box.findData(attr.slug if attr else None)
            row.box.setCurrentIndex(max(index, 0))
            row.value.setValue(attr.value if attr else 0.0)
        self.mr.setValue(draft.mr)
        self.rolls.setValue(draft.rolls)
        self.rank.setValue(draft.rank)
        self.polarity.setCurrentIndex(max(self.polarity.findData(draft.polarity), 0))

    def run_check(self) -> None:
        weapon = self.window_.weapon
        if weapon is None:
            self.detail.setText("Search for the riven's weapon first.")
            return
        draft = self.draft()
        self._request += 1
        request = self._request
        me = self.window_.account.ingame_name if self.window_.account.signed_in else None
        self.result.setText("")
        self.detail.setText("Checking listings…")

        def work() -> PriceCheck:
            auctions = self.window_.service.search(weapon.slug)
            return price_check(auctions, draft.positives, draft.negative, exclude_seller=me)

        self.window_.run_async(work, lambda check: self._show(request, check),
                               lambda exc: self._failed(request, exc))

    def _failed(self, request: int, exc: Exception) -> None:
        if request == self._request:
            self.detail.setText(f"Could not load listings: {exc}")

    def _show(self, request: int, check: PriceCheck) -> None:
        if request != self._request:
            return
        self.check = check
        fill_auction_table(self.table, self.window_.catalog, list(check.comparables))
        self.sell_button.setEnabled(True)
        if check.estimate is None:
            self.result.setText("No listings to compare")
            self.detail.setText("Nobody is selling a riven for this weapon right now.")
            return
        self.result.setText(f"About {check.estimate:g} platinum")
        extra = f" {len(check.outliers)} extreme price(s) left out." if check.outliers else ""
        if check.exact and check.tier not in ("same stats and curse", "same stats, no curse", "same positive stats"):
            prices = ", ".join(f"{a.price}p" for a in check.exact[:5])
            extra += f" Only {len(check.exact)} with exactly your positive stats: {prices}."
        self.detail.setText(
            f"Median of the cheapest {min(check.count, 5)} of {check.count} listings with {check.tier} "
            f"(range {check.low}-{check.high}p). Rivens have no trade history: these are asking prices.{extra}")


class RivenWindow(QWidget):
    _async_done = pyqtSignal(object, object)  # (callback, result) run on the UI thread

    def __init__(self, catalog: RivenCatalog, service: RivenService, account: Optional[Account] = None) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace rivens")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE + BUTTON_STYLE)
        self.resize(980, 680)
        self.catalog = catalog
        self.service = service
        self.account = account if account is not None else Account(service.client)
        self.weapon: Optional[RivenWeapon] = None
        self._executor = ThreadPoolExecutor(max_workers=2)

        self.search = QLineEdit(placeholderText="Search a weapon, e.g. kulstar")
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(180)
        self.suggestions.hide()
        self.title = QLabel("Pick a weapon to see its rivens.", objectName="title")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.toast = QLabel(objectName="toast")
        self._toast_timer = QTimer(self, singleShot=True, interval=4000)
        self._toast_timer.timeout.connect(lambda: self.toast.setText(""))

        self.find_panel = FindPanel(self)
        self.price_panel = PricePanel(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.find_panel, "Find rivens")
        self.tabs.addTab(self.price_panel, "Price check")

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.suggestions)
        layout.addWidget(self.title)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.toast)
        self._async_done.connect(lambda callback, result: callback(result))

        self.search.textEdited.connect(self._on_search)
        self.search.returnPressed.connect(self._pick_first_suggestion)
        self.suggestions.itemClicked.connect(lambda it: self.open_weapon(it.data(Qt.ItemDataRole.UserRole)))

    # --- weapon search ------------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self.suggestions.clear()
        hits = search_catalog(text, self.catalog.weapons)
        for weapon in hits:
            self.suggestions.addItem(weapon.name)
            self.suggestions.item(self.suggestions.count() - 1).setData(Qt.ItemDataRole.UserRole, weapon.slug)
        self.suggestions.setVisible(bool(hits))

    def _pick_first_suggestion(self) -> None:
        if self.suggestions.count():
            self.open_weapon(self.suggestions.item(0).data(Qt.ItemDataRole.UserRole))

    def open_weapon(self, slug: str) -> None:
        weapon = self.catalog.by_weapon.get(slug)
        if weapon is None:
            return
        self.weapon = weapon
        self.suggestions.hide()
        self.search.setText(weapon.name)
        self.title.setText(weapon_title(weapon))
        self.price_panel.set_weapon(weapon)
        self.find_panel.set_weapon(weapon)

    def load_draft(self, draft: RivenDraft) -> None:
        """Open the price check filled in with this riven."""
        if draft.weapon:
            self.open_weapon(draft.weapon)
        self.price_panel.load_draft(draft)
        self.tabs.setCurrentWidget(self.price_panel)

    # --- plumbing ------------------------------------------------------------------------

    def show_toast(self, text: str) -> None:
        self.toast.setText(text)
        self._toast_timer.start()

    def run_async(self, fn, on_ok, on_err=None) -> None:
        """Run `fn` off the UI thread; call on_ok(result) or on_err(exception) back on the UI thread."""
        def work():
            try:
                self._async_done.emit(on_ok, fn())
            except Exception as exc:
                log.warning("riven request failed: %s", exc)
                if on_err is not None:
                    self._async_done.emit(on_err, exc)

        self._executor.submit(work)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()  # closing only hides it; the app keeps running in the tray
        self.hide()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
