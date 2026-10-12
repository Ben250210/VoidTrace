"""The Relics section of the main window: what a relic is worth on average, so you know which to open.

  * Unvaulted / Vaulted - every relic that drops now / no longer drops, with its value per refinement
  * Look up             - one relic (search by name or by a reward): value at each refinement and its
                          6 rewards with price and chance

Values come from relics.py. Prices come from the same PriceService (and cache) as the reward overlay,
loaded in the background a few at a time. "Solo", or "Squad of 4": the best of 4 drops.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QObject, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QVBoxLayout, QWidget

from ..icons import chest_icon, ducat_icon, platinum_icon
from ..items import Item
from ..price_cleaner import PriceService
from ..relics import ERAS, REFINEMENTS, Relic, Reward, chance, load_relics, relic_value, search_relics
from .widgets import Section, make_cell, make_table, set_rows

log = logging.getLogger(__name__)

RARITY_COLOR = {"common": QColor("#c49a6c"), "uncommon": QColor("#c0c8d4"), "rare": QColor("#e0b64f")}


def fmt(value: float, missing: int = 0) -> str:
    text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    return text + ("*" if missing else "")


class RelicPrices(QObject):
    """Plat and ducat values of relic rewards, shared by every Relics page. Cached prices show at once
    (however old); fresh ones are fetched in the background and `changed` fires (at most every 250 ms)
    as they arrive."""

    changed = pyqtSignal()
    _price_ready = pyqtSignal(str, object)  # slug, price (None when unknown)

    def __init__(self, items: Sequence[Item], prices: Optional[PriceService]) -> None:
        super().__init__()
        self.by_name = {i.name.casefold(): i for i in items}
        self.prices = prices
        self.plat: dict[str, Optional[float]] = {}
        self.pending: set[str] = set()
        self.requested: set[str] = set()  # fetched live, or being fetched
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="VoidTrace-Relics")
        self._price_ready.connect(self._on_price)
        # Redraw at most every 1.5 s while prices stream in: redrawing 750 rows per price pinned a core.
        self._changed_soon = QTimer(self, singleShot=True, interval=1500)
        self._changed_soon.timeout.connect(self.changed.emit)

    def item_for(self, reward: Reward) -> Optional[Item]:
        key = reward.name.casefold()
        return self.by_name.get(key) or self.by_name.get(key + " blueprint")

    def plat_of(self, reward: Reward) -> Optional[float]:
        """Plat for one reward: None while still loading, 0 for untradable rewards (Forma)."""
        item = self.item_for(reward)
        if item is None or item.slug is None:
            return 0.0
        return self.plat.get(item.slug)

    def ducats_of(self, reward: Reward) -> float:
        item = self.item_for(reward)
        return float(item.ducats) if item else 0.0

    def load(self, relics: Sequence[Relic]) -> None:
        """Show cached prices for these relics' rewards now, and fetch fresh ones in the background."""
        if self.prices is None:
            return
        slugs = []
        for relic in relics:
            for reward in relic.rewards:
                item = self.item_for(reward)
                if item is not None and item.slug is not None and item.slug not in self.requested \
                        and item.slug not in slugs:
                    slugs.append(item.slug)
        if not slugs:
            return
        cached = self._cached([s for s in slugs if s not in self.plat])
        for slug in slugs:
            self.requested.add(slug)
            self.pending.add(slug)
            if slug in cached:
                self.plat[slug] = cached[slug].price
            self.executor.submit(self._fetch, slug)
        if cached:
            self.changed.emit()

    def _cached(self, slugs: list[str]) -> dict:
        """Cached prices for these slugs, read in one go."""
        try:
            return self.prices.cached_many(slugs) if hasattr(self.prices, "cached_many") else {}
        except Exception:
            return {}

    def progress(self) -> tuple[int, int]:
        """(prices fetched, prices asked for)."""
        return len(self.requested) - len(self.pending), len(self.requested)

    def _fetch(self, slug: str) -> None:
        try:
            stats = self.prices.get_price(slug)
        except Exception as exc:
            log.warning("relic prices: no price for %s: %s", slug, exc)
            stats = None
        self._price_ready.emit(slug, stats.price if stats is not None else None)

    def _on_price(self, slug: str, price) -> None:
        self.pending.discard(slug)
        if price is not None or slug not in self.plat:  # keep a cached price if the fetch failed
            self.plat[slug] = price
        if not self._changed_soon.isActive():
            self._changed_soon.start()

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class RelicTablePage(QWidget):
    """Every vaulted (or every unvaulted) relic with its value. Click a column header to sort by it
    (again to flip); double-click a relic for its rewards."""

    COLUMNS = ["", "Relic", "Era", "Intact plat", "Radiant plat", "Intact ducats", "Radiant ducats", "Best reward"]
    CHEST, NAME, ERA, INTACT, RADIANT, INTACT_DUCATS, RADIANT_DUCATS, BEST = range(8)
    TEXT_COLUMNS = {CHEST, NAME, ERA, BEST}  # sorted A-Z first; numbers start highest first
    open_relic = pyqtSignal(str)

    def __init__(self, vaulted: bool, data: RelicPrices, relics: Sequence[Relic],
                 squad: Callable[[], int]) -> None:
        super().__init__()
        self.vaulted = vaulted
        self.data = data
        self.relics = [r for r in relics if r.vaulted is vaulted]
        self.squad = squad
        self.loaded = False
        self.rows: list[dict] = []
        self.sort_col, self.sort_desc = self.RADIANT, True
        self.table = make_table(self.COLUMNS)
        self.table.setIconSize(QSize(18, 18))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.CHEST, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.BEST, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self.sort_by)
        for col in (self.INTACT, self.RADIANT):
            self.table.horizontalHeaderItem(col).setIcon(QIcon(platinum_icon(14)))
        for col in (self.INTACT_DUCATS, self.RADIANT_DUCATS):
            self.table.horizontalHeaderItem(col).setIcon(QIcon(ducat_icon(14)))
        self.table.cellDoubleClicked.connect(lambda row, _c: self.open_relic.emit(self.table.item(row, self.NAME).text()))
        self.status = QLabel(objectName="muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        self._update_header()

    def shown(self) -> None:
        """The page came into view. Prices load only once it is really on screen: a hidden window
        must not keep the API busy while the reward overlay needs it."""
        if not self.loaded and self.isVisible():
            self.loaded = True
            self.data.load(self.relics)
        self.render()

    def sort_by(self, column: int) -> None:
        if column == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = column, column not in self.TEXT_COLUMNS
        self._update_header()
        self._fill()

    def _update_header(self) -> None:
        for col, text in enumerate(self.COLUMNS):
            arrow = (" \u25bc" if self.sort_desc else " \u25b2") if col == self.sort_col and text else ""
            self.table.horizontalHeaderItem(col).setText(text + arrow)
            self.table.horizontalHeaderItem(col).setToolTip("Click to sort")

    def _row(self, relic: Relic) -> dict:
        squad = self.squad()
        values = {}
        for col, value_of, refinement in ((self.INTACT, self.data.plat_of, "intact"),
                                          (self.RADIANT, self.data.plat_of, "radiant"),
                                          (self.INTACT_DUCATS, self.data.ducats_of, "intact"),
                                          (self.RADIANT_DUCATS, self.data.ducats_of, "radiant")):
            value, missing = relic_value(relic, value_of, refinement, squad)
            values[col] = (value, missing)
        priced = [(self.data.plat_of(r), r.name) for r in relic.rewards]
        known = [(p, n) for p, n in priced if p is not None]
        best = max(known)[1] if known and len(known) == len(priced) else "\u2026"
        return {"relic": relic, "values": values, "best": best}

    def sort_value(self, row: dict, column: int):
        """What a row sorts by in `column`; None (sorted last) while a plat value is still loading."""
        relic = row["relic"]
        if column == self.CHEST:
            return relic.name
        if column == self.NAME:
            return relic.name
        if column == self.ERA:
            return (ERAS.index(relic.era) if relic.era in ERAS else len(ERAS), relic.name)
        if column == self.BEST:
            return None if row["best"] == "\u2026" else row["best"]
        value, missing = row["values"][column]
        return None if missing else value

    def render(self) -> None:
        self.rows = [self._row(relic) for relic in self.relics]
        self._fill()

    def _fill(self) -> None:
        known = [r for r in self.rows if self.sort_value(r, self.sort_col) is not None]
        unknown = [r for r in self.rows if self.sort_value(r, self.sort_col) is None]
        known.sort(key=lambda r: self.sort_value(r, self.sort_col), reverse=self.sort_desc)
        unknown.sort(key=lambda r: r["relic"].name)
        chest = QIcon(chest_icon(32, self.vaulted))
        tip = "Vaulted: no longer drops" if self.vaulted else "Drops now"
        self.table.setUpdatesEnabled(False)  # one repaint for the whole table, not one per cell
        set_rows(self.table, len(self.rows))
        for i, row in enumerate(known + unknown):
            relic = row["relic"]
            chest_cell = make_cell("")
            chest_cell.setIcon(chest)
            chest_cell.setToolTip(tip)
            self.table.setItem(i, self.CHEST, chest_cell)
            self.table.setItem(i, self.NAME, make_cell(relic.name))
            self.table.setItem(i, self.ERA, make_cell(relic.era))
            for col in (self.INTACT, self.RADIANT, self.INTACT_DUCATS, self.RADIANT_DUCATS):
                value, missing = row["values"][col]
                self.table.setItem(i, col, make_cell(fmt(value, missing), right=True))
            self.table.setItem(i, self.BEST, make_cell(row["best"]))
        self.table.setUpdatesEnabled(True)
        done, asked = self.data.progress()
        missing = any(row["values"][self.RADIANT][1] for row in self.rows)
        if asked - done:
            text = f"Loading prices {done}/{asked}\u2026"
        else:
            text = "double-click one for its rewards" + (" (* = some prices not loaded yet)" if missing else "")
        self.status.setText(f"{len(self.rows)} relics \u00b7 {text}")


class RelicLookupPage(QWidget):
    """One relic: its value at each refinement, and its rewards with price and chance."""

    def __init__(self, data: RelicPrices, relics: Sequence[Relic], squad: Callable[[], int]) -> None:
        super().__init__()
        self.data = data
        self.relics = list(relics)
        self.squad = squad
        self.relic: Optional[Relic] = None
        self.search = QLineEdit(placeholderText="Search a relic (axi a1) or a reward (nikana prime)")
        self.search.textEdited.connect(self._on_search)
        self.results = QListWidget()
        self.results.setMaximumWidth(230)
        self.results.currentTextChanged.connect(self.pick)
        self.title = QLabel("Pick a relic", objectName="title")
        self.chest = QLabel()
        self.note = QLabel(objectName="muted", wordWrap=True)
        self.summary = make_table(["Value"] + [r.title() for r in REFINEMENTS])
        self.summary.setRowCount(2)
        self.summary.setMaximumHeight(96)
        self.rewards = make_table(["Reward", "Rarity", "Plat", "Ducats", "Intact", "Radiant"])
        self.rewards.horizontalHeaderItem(2).setIcon(QIcon(platinum_icon(14)))
        self.rewards.horizontalHeaderItem(3).setIcon(QIcon(ducat_icon(14)))
        detail = QVBoxLayout()
        title_row = QHBoxLayout()
        title_row.addWidget(self.chest)
        title_row.addWidget(self.title, 1)
        detail.addLayout(title_row)
        detail.addWidget(self.summary)
        detail.addWidget(self.rewards, 1)
        detail.addWidget(self.note)
        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.results, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(left)
        layout.addLayout(detail, 1)
        self._list([r for r in self.relics if r.vaulted is False])

    def _on_search(self, text: str) -> None:
        self._list(search_relics(text, self.relics) if text.strip() else [r for r in self.relics if r.vaulted is False])

    def _list(self, relics: Sequence[Relic]) -> None:
        self.results.blockSignals(True)
        self.results.clear()
        for relic in relics:
            self.results.addItem(relic.name)
        self.results.blockSignals(False)
        if relics and (self.relic is None or self.search.text().strip()):
            self.results.setCurrentRow(0)
            self.pick(relics[0].name)

    def pick(self, name: str) -> None:
        relic = next((r for r in self.relics if r.name == name), None)
        if relic is not None:
            self.relic = relic
            self.shown()

    def shown(self) -> None:
        if self.relic is not None and self.isVisible():
            self.data.load([self.relic])
        self.render()

    def show_relic(self, name: str) -> None:
        self.search.setText(name.replace(" Relic", ""))
        self._on_search(self.search.text())
        self.pick(name)

    def render(self) -> None:
        relic, squad = self.relic, self.squad()
        if relic is None:
            return
        vault = {True: "  ·  vaulted", False: "  ·  farmable now"}.get(relic.vaulted, "")
        self.title.setText(relic.name + vault)
        if relic.vaulted is None:
            self.chest.clear()
        else:
            self.chest.setPixmap(chest_icon(24, relic.vaulted))
        missing_any = 0
        for row, (label, value_of, icon) in enumerate((("Plat", self.data.plat_of, platinum_icon(14)),
                                                       ("Ducats", self.data.ducats_of, ducat_icon(14)))):
            self.summary.setItem(row, 0, make_cell(label, icon=icon))
            for col, refinement in enumerate(REFINEMENTS, start=1):
                value, missing = relic_value(relic, value_of, refinement, squad)
                missing_any += missing
                self.summary.setItem(row, col, make_cell(fmt(value, missing), right=True))
        set_rows(self.rewards, len(relic.rewards))
        order = sorted(relic.rewards, key=lambda r: ("rare", "uncommon", "common").index(r.rarity)
                       if r.rarity in ("rare", "uncommon", "common") else 3)
        for row, reward in enumerate(order):
            plat = self.data.plat_of(reward)
            name = reward.name + (f" ×{reward.count}" if reward.count > 1 else "")
            self.rewards.setItem(row, 0, make_cell(name))
            self.rewards.setItem(row, 1, make_cell(reward.rarity.title(), color=RARITY_COLOR.get(reward.rarity)))
            self.rewards.setItem(row, 2, make_cell("…" if plat is None else fmt(plat), right=True))
            self.rewards.setItem(row, 3, make_cell(fmt(self.data.ducats_of(reward)), right=True))
            for col, refinement in ((4, "intact"), (5, "radiant")):
                self.rewards.setItem(row, col, make_cell(f"{chance(reward.rarity, refinement) * 100:.0f}%",
                                                         right=True))
        how = "best of 4 drops (a squad opening the same relic)" if squad == 4 else "one relic, opened alone"
        self.note.setText(f"Average value per relic: {how}. "
                          + ("* still loading some prices. " if missing_any else "")
                          + "Forma and other untradable rewards count as 0 plat.")


class RelicSection(Section):
    PAGES = {"unvaulted": "Unvaulted", "vaulted": "Vaulted", "lookup": "Look up"}

    def __init__(self, items: Sequence[Item], prices: Optional[PriceService],
                 relics: Optional[list[Relic]] = None) -> None:
        super().__init__()
        self.relics = relics if relics is not None else load_relics()
        self.data = RelicPrices(items, prices)
        self.squad = QComboBox()
        self.squad.addItem("Solo", 1)
        self.squad.addItem("Squad of 4 (best drop)", 4)
        squad = self.squad.currentData
        self.unvaulted = RelicTablePage(False, self.data, self.relics, squad)
        self.vaulted = RelicTablePage(True, self.data, self.relics, squad)
        self.lookup = RelicLookupPage(self.data, self.relics, squad)
        for key, page in (("unvaulted", self.unvaulted), ("vaulted", self.vaulted), ("lookup", self.lookup)):
            self.add_page(key, page)
        for table_page in (self.unvaulted, self.vaulted):
            table_page.open_relic.connect(self.show_relic)
        self.squad.currentIndexChanged.connect(lambda _: self.render())
        self.data.changed.connect(self.render)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.squad)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.pages, 1)

    def page_shown(self, key: str) -> None:
        self.page(key).shown()

    def show_relic(self, name: str) -> None:
        self.show_page("lookup")
        self.lookup.show_relic(name)

    def render(self) -> None:
        current = self.pages.currentWidget()
        if current is not None:
            current.render()

    def shutdown(self) -> None:
        super().shutdown()
        self.data.shutdown()
