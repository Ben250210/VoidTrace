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

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QVBoxLayout, QWidget

from ..icons import ducat_icon, platinum_icon
from ..items import Item
from ..price_cleaner import PriceService
from ..relics import REFINEMENTS, Relic, Reward, chance, load_relics, relic_value, search_relics
from .widgets import Section, make_cell, make_table

log = logging.getLogger(__name__)

RARITY_COLOR = {"common": QColor("#c49a6c"), "uncommon": QColor("#c0c8d4"), "rare": QColor("#e0b64f")}


def fmt(value: float, missing: int = 0) -> str:
    text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    return text + ("*" if missing else "")


class RelicPrices(QObject):
    """Plat and ducat values of relic rewards, shared by every Relics page. `changed` fires (at most
    every 250 ms) as prices arrive."""

    changed = pyqtSignal()
    _price_ready = pyqtSignal(str, object)  # slug, price (None when unknown)

    def __init__(self, items: Sequence[Item], prices: Optional[PriceService]) -> None:
        super().__init__()
        self.by_name = {i.name.casefold(): i for i in items}
        self.prices = prices
        self.plat: dict[str, Optional[float]] = {}
        self.pending: set[str] = set()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="VoidTrace-Relics")
        self._price_ready.connect(self._on_price)
        self._changed_soon = QTimer(self, singleShot=True, interval=250)
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
        """Fetch the prices of these relics' rewards in the background."""
        if self.prices is None:
            return
        for relic in relics:
            for reward in relic.rewards:
                item = self.item_for(reward)
                if item is None or item.slug is None or item.slug in self.plat or item.slug in self.pending:
                    continue
                self.pending.add(item.slug)
                self.executor.submit(self._fetch, item.slug)

    def _fetch(self, slug: str) -> None:
        try:
            stats = self.prices.get_price(slug)
        except Exception as exc:
            log.warning("relic prices: no price for %s: %s", slug, exc)
            stats = None
        self._price_ready.emit(slug, stats.price if stats is not None else None)

    def _on_price(self, slug: str, price) -> None:
        self.pending.discard(slug)
        self.plat[slug] = price
        self._changed_soon.start()

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class RelicTablePage(QWidget):
    """Every vaulted (or every unvaulted) relic with its value. Double-click a relic for its rewards."""

    open_relic = pyqtSignal(str)

    def __init__(self, vaulted: bool, data: RelicPrices, relics: Sequence[Relic],
                 squad: Callable[[], int]) -> None:
        super().__init__()
        self.vaulted = vaulted
        self.data = data
        self.relics = [r for r in relics if r.vaulted is vaulted]
        self.squad = squad
        self.loaded = False
        self.table = make_table(["Relic", "Intact", "Radiant", "Radiant ducats"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda row, _c: self.open_relic.emit(self.table.item(row, 0).text()))
        self.status = QLabel(objectName="muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)

    def shown(self) -> None:
        if not self.loaded:
            self.loaded = True
            self.data.load(self.relics)
        self.render()

    def render(self) -> None:
        squad = self.squad()
        rows = []
        for relic in self.relics:
            intact, m1 = relic_value(relic, self.data.plat_of, "intact", squad)
            radiant, m2 = relic_value(relic, self.data.plat_of, "radiant", squad)
            ducats, _ = relic_value(relic, self.data.ducats_of, "radiant", squad)
            rows.append((radiant, relic.name, intact, ducats, m1 + m2))
        rows.sort(key=lambda r: -r[0])
        self.table.setRowCount(len(rows))
        for row, (radiant, name, intact, ducats, missing) in enumerate(rows):
            self.table.setItem(row, 0, make_cell(name))
            self.table.setItem(row, 1, make_cell(fmt(intact, missing), right=True, icon=platinum_icon(14)))
            self.table.setItem(row, 2, make_cell(fmt(radiant, missing), right=True, icon=platinum_icon(14)))
            self.table.setItem(row, 3, make_cell(fmt(ducats), right=True, icon=ducat_icon(14)))
        loading = len(self.data.pending)
        self.status.setText(f"{len(rows)} relics. " + (f"Loading {loading} prices…" if loading else
                                                       "Double-click one to see its rewards."))


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
        self.note = QLabel(objectName="muted", wordWrap=True)
        self.summary = make_table(["Value"] + [r.title() for r in REFINEMENTS])
        self.summary.setRowCount(2)
        self.summary.setMaximumHeight(96)
        self.rewards = make_table(["Reward", "Rarity", "Plat", "Ducats", "Intact", "Radiant"])
        self.rewards.horizontalHeaderItem(2).setIcon(QIcon(platinum_icon(14)))
        self.rewards.horizontalHeaderItem(3).setIcon(QIcon(ducat_icon(14)))
        detail = QVBoxLayout()
        detail.addWidget(self.title)
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
            self.data.load([relic])
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
        missing_any = 0
        for row, (label, value_of, icon) in enumerate((("Plat", self.data.plat_of, platinum_icon(14)),
                                                       ("Ducats", self.data.ducats_of, ducat_icon(14)))):
            self.summary.setItem(row, 0, make_cell(label, icon=icon))
            for col, refinement in enumerate(REFINEMENTS, start=1):
                value, missing = relic_value(relic, value_of, refinement, squad)
                missing_any += missing
                self.summary.setItem(row, col, make_cell(fmt(value, missing), right=True))
        self.rewards.setRowCount(len(relic.rewards))
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
        page = self.page(key)
        if isinstance(page, RelicTablePage):
            page.shown()
        else:
            page.render()

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
