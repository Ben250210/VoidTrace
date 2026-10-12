"""The Relics window (tray > Relics): what a relic is worth on average, so you know which to open.

  * Look up: search a relic (or a reward: "nikana prime"); see its plat and ducat value at each
    refinement, and its 6 rewards with their price and chances.
  * Best unvaulted: the relics you can farm now, sorted by Radiant value.

Values come from relics.py; prices from the same PriceService (and cache) as the reward overlay,
loaded in the background a few at a time. Solo, or "squad of 4": the best of 4 drops.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton,
                             QTabWidget, QVBoxLayout, QWidget)

from .. import theme
from ..icons import ducat_icon, platinum_icon
from ..items import Item
from .widgets import STYLE
from ..price_cleaner import PriceService
from ..relics import REFINEMENTS, Relic, Reward, chance, load_relics, relic_value, search_relics
from .widgets import BUTTON_STYLE, make_cell, make_table

log = logging.getLogger(__name__)

RARITY_COLOR = {"common": QColor("#c49a6c"), "uncommon": QColor("#c0c8d4"), "rare": QColor("#e0b64f")}


def fmt(value: float, missing: int = 0) -> str:
    text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
    return text + ("*" if missing else "")


class RelicWindow(QWidget):
    price_ready = pyqtSignal(str, object)  # slug, price (None when unknown)

    def __init__(self, items: Sequence[Item], prices: PriceService, relics: Optional[list[Relic]] = None) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace relics")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        theme.follow(self, STYLE + BUTTON_STYLE)
        self.resize(760, 560)
        self.relics = relics if relics is not None else load_relics()
        self.by_name = {i.name.casefold(): i for i in items}
        self.prices = prices
        self.plat: dict[str, Optional[float]] = {}
        self.pending: set[str] = set()
        self.relic: Optional[Relic] = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="VoidTrace-Relics")
        self.price_ready.connect(self._on_price)
        self._render_soon = QTimer(self, singleShot=True, interval=250)
        self._render_soon.timeout.connect(self.render)

        self.search = QLineEdit(placeholderText="Search a relic (axi a1) or a reward (nikana prime)")
        self.search.textEdited.connect(self._on_search)
        self.squad = QComboBox()
        self.squad.addItem("Solo", 1)
        self.squad.addItem("Squad of 4 (best drop)", 4)
        self.squad.currentIndexChanged.connect(lambda _: self.render())
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.squad)

        # Look up
        self.results = QListWidget()
        self.results.setMaximumWidth(230)
        self.results.currentTextChanged.connect(self._pick)
        self.title = QLabel(objectName="title")
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
        lookup = QWidget()
        lookup_layout = QHBoxLayout(lookup)
        lookup_layout.addWidget(self.results)
        lookup_layout.addLayout(detail, 1)

        # Best unvaulted
        self.best = make_table(["Relic", "Intact", "Radiant", "Radiant ducats"])
        self.best.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.best.cellDoubleClicked.connect(lambda row, _c: self.show_relic(self.best.item(row, 0).text()))
        self.load_best = QPushButton("Load prices", objectName="primary")
        self.load_best.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_best.clicked.connect(self.fetch_unvaulted)
        self.best_status = QLabel(objectName="muted")
        best_top = QHBoxLayout()
        best_top.addWidget(self.best_status, 1)
        best_top.addWidget(self.load_best)
        best_tab = QWidget()
        best_layout = QVBoxLayout(best_tab)
        best_layout.addLayout(best_top)
        best_layout.addWidget(self.best, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(lookup, "Look up")
        self.tabs.addTab(best_tab, "Best unvaulted")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs, 1)
        self._list(self.unvaulted())
        self.render()

    # --- data ---------------------------------------------------------------------------------

    def unvaulted(self) -> list[Relic]:
        return [r for r in self.relics if r.vaulted is False]

    def item_for(self, reward: Reward) -> Optional[Item]:
        key = reward.name.casefold()
        return self.by_name.get(key) or self.by_name.get(key + " blueprint")

    def plat_of(self, reward: Reward) -> Optional[float]:
        item = self.item_for(reward)
        if item is None or item.slug is None:
            return 0.0  # untradable (Forma) or not on the market
        return self.plat.get(item.slug)

    def ducats_of(self, reward: Reward) -> float:
        item = self.item_for(reward)
        return float(item.ducats) if item else 0.0

    def fetch(self, relics: Sequence[Relic]) -> None:
        for relic in relics:
            for reward in relic.rewards:
                item = self.item_for(reward)
                if item is None or item.slug is None or item.slug in self.plat or item.slug in self.pending:
                    continue
                self.pending.add(item.slug)
                self.executor.submit(self._load, item.slug)

    def _load(self, slug: str) -> None:
        try:
            stats = self.prices.get_price(slug)
        except Exception as exc:
            log.warning("relic planner: no price for %s: %s", slug, exc)
            stats = None
        self.price_ready.emit(slug, stats.price if stats is not None else None)

    def _on_price(self, slug: str, price) -> None:
        self.pending.discard(slug)
        self.plat[slug] = price
        self._render_soon.start()

    def fetch_unvaulted(self) -> None:
        self.fetch(self.unvaulted())
        self.render()

    # --- view ----------------------------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self._list(search_relics(text, self.relics) if text.strip() else self.unvaulted())

    def _list(self, relics: Sequence[Relic]) -> None:
        self.results.blockSignals(True)
        self.results.clear()
        for relic in relics:
            self.results.addItem(relic.name)
        self.results.blockSignals(False)
        if relics and (self.relic is None or self.search.text().strip()):
            self.results.setCurrentRow(0)
            self._pick(relics[0].name)

    def _pick(self, name: str) -> None:
        relic = next((r for r in self.relics if r.name == name), None)
        if relic is not None:
            self.relic = relic
            self.fetch([relic])
            self.render()

    def show_relic(self, name: str) -> None:
        self.tabs.setCurrentIndex(0)
        self.search.setText(name.replace(" Relic", ""))
        self._on_search(self.search.text())

    def render(self) -> None:
        self._render_relic()
        self._render_best()

    def _render_relic(self) -> None:
        relic, squad = self.relic, self.squad.currentData()
        if relic is None:
            self.title.setText("Pick a relic")
            return
        vault = {True: "  ·  vaulted", False: "  ·  farmable now"}.get(relic.vaulted, "")
        self.title.setText(relic.name + vault)
        missing_any = 0
        for row, (label, value_of, icon) in enumerate((("Plat", self.plat_of, platinum_icon(14)),
                                                       ("Ducats", self.ducats_of, ducat_icon(14)))):
            self.summary.setItem(row, 0, make_cell(label, icon=icon))
            for col, refinement in enumerate(REFINEMENTS, start=1):
                value, missing = relic_value(relic, value_of, refinement, squad)
                missing_any += missing
                self.summary.setItem(row, col, make_cell(fmt(value, missing), right=True))
        self.rewards.setRowCount(len(relic.rewards))
        order = sorted(relic.rewards, key=lambda r: ("rare", "uncommon", "common").index(r.rarity)
                       if r.rarity in ("rare", "uncommon", "common") else 3)
        for row, reward in enumerate(order):
            plat = self.plat_of(reward)
            name = reward.name + (f" ×{reward.count}" if reward.count > 1 else "")
            self.rewards.setItem(row, 0, make_cell(name))
            self.rewards.setItem(row, 1, make_cell(reward.rarity.title(), color=RARITY_COLOR.get(reward.rarity)))
            self.rewards.setItem(row, 2, make_cell("…" if plat is None else fmt(plat), right=True))
            self.rewards.setItem(row, 3, make_cell(fmt(self.ducats_of(reward)), right=True))
            for col, refinement in ((4, "intact"), (5, "radiant")):
                self.rewards.setItem(row, col, make_cell(f"{chance(reward.rarity, refinement) * 100:.0f}%", right=True))
        how = "best of 4 drops (a squad opening the same relic)" if squad == 4 else "one relic, opened alone"
        self.note.setText(f"Average value per relic: {how}. "
                          + ("* still loading some prices. " if missing_any else "")
                          + "Forma and other untradable rewards count as 0 plat.")

    def _render_best(self) -> None:
        squad = self.squad.currentData()
        rows = []
        for relic in self.unvaulted():
            intact, m1 = relic_value(relic, self.plat_of, "intact", squad)
            radiant, m2 = relic_value(relic, self.plat_of, "radiant", squad)
            ducats, _ = relic_value(relic, self.ducats_of, "radiant", squad)
            rows.append((radiant, relic.name, intact, ducats, m1 + m2))
        rows.sort(key=lambda r: -r[0])
        self.best.setRowCount(len(rows))
        for row, (radiant, name, intact, ducats, missing) in enumerate(rows):
            self.best.setItem(row, 0, make_cell(name))
            self.best.setItem(row, 1, make_cell(fmt(intact, missing), right=True, icon=platinum_icon(14)))
            self.best.setItem(row, 2, make_cell(fmt(radiant, missing), right=True, icon=platinum_icon(14)))
            self.best.setItem(row, 3, make_cell(fmt(ducats), right=True, icon=ducat_icon(14)))
        loading = len(self.pending)
        self.best_status.setText(f"{len(rows)} relics drop now. "
                                 + (f"Loading {loading} prices…" if loading else
                                    "Double-click one to see its rewards. * = some prices not loaded yet."))

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
