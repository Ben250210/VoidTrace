"""The Rivens section of the main window: pick a weapon, then
  * Find         - riven auctions for it, filtered by stats, curse and rolls (riven_find.py)
  * Price check  - your riven's stats, typed or read off the screen, priced against comparable ones
                   (riven_price.py)
  * Sell         - post it as a Warframe.Market auction (riven_trade.py)
  * My rivens    - your riven auctions: show/hide, match the cheapest, close (riven_trade.py)
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QLineEdit, QListWidget, QVBoxLayout

from ..account import Account
from ..search import search_catalog
from ..rivens import PriceCheck, RivenCatalog, RivenDraft, RivenService, RivenWeapon
from .widgets import Section, guard_wheel
from .riven_find import FindPanel, weapon_title
from .riven_price import PricePanel

log = logging.getLogger(__name__)


class RivenSection(Section):
    PAGES = {"find": "Find", "price": "Price check", "sell": "Sell", "mine": "My rivens"}

    def __init__(self, catalog: RivenCatalog, service: RivenService, account: Optional[Account] = None) -> None:
        super().__init__()
        self.catalog = catalog
        self.service = service
        self.account = account if account is not None else Account(service.client)
        self.weapon: Optional[RivenWeapon] = None

        self.search = QLineEdit(placeholderText="Search a weapon, e.g. kulstar")
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(180)
        self.suggestions.hide()
        self.title = QLabel("Pick a weapon to see its rivens.", objectName="title")
        self.title.setTextFormat(Qt.TextFormat.RichText)

        from .riven_trade import MyRivensPanel, SellPanel  # imported here: it imports this module's types

        self.find_panel = FindPanel(self)
        self.price_panel = PricePanel(self)
        self.sell_panel = SellPanel(self)
        self.my_rivens = MyRivensPanel(self)
        for key, page in (("find", self.find_panel), ("price", self.price_panel), ("sell", self.sell_panel),
                          ("mine", self.my_rivens)):
            self.add_page(key, page)
        self.price_panel.sell_requested.connect(self.open_sell)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.suggestions)
        layout.addWidget(self.title)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.toast)
        self.refresh_account()
        self._wheel_guard = guard_wheel(self)

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
        self.show_page("price")

    def read_from_frame(self, frame) -> None:
        """Read the riven card on a screenshot (Ctrl+Shift+R in game) into the price check."""
        from ..riven_ocr import read_riven

        self.show_toast("Reading the riven\u2026")
        self.run_async(lambda: read_riven(frame, self.catalog), self._riven_read,
                       lambda exc: self.show_toast(f"Could not read the riven: {exc}"))

    def _riven_read(self, read) -> None:
        if not read.ok:
            self.show_toast(" ".join(read.warnings))
            return
        self.load_draft(read.draft)
        self.show_toast("")
        if read.warnings:
            self.price_panel._note("Read from the screen. " + " ".join(read.warnings), warn=True)
        else:
            self.price_panel._note("Read from the screen. Check it against the game.")
        self.price_panel.run_check()

    def open_sell(self, draft: RivenDraft, check: Optional[PriceCheck]) -> None:
        self.sell_panel.load(draft, check)
        self.show_page("sell")

    # --- pages and account ---------------------------------------------------------------

    def refresh_account(self) -> None:
        self.sell_panel.update_state()
        if self.current_page() == "mine":
            self.my_rivens.refresh()

    def page_shown(self, key: str) -> None:
        if key == "mine":
            self.my_rivens.refresh()
        elif key == "sell":
            self.sell_panel.update_state()
