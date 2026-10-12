"""Riven window: find riven auctions, price-check your riven, and sell it.

A separate window from the market window (rivens are auctions, not orders), with the same look:
  * Find rivens  - auctions for a weapon, filtered by wanted stats and curse; click to copy the whisper
  * Price check  - type in your riven's stats; the estimate comes from comparable listings
  * Sell         - post it as a Warframe.Market auction (signed in; see riven_trade.py)
  * My rivens    - your riven auctions: show/hide, match the cheapest, close
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..account import Account
from ..search import search_catalog
from .widgets import STYLE
from ..rivens import PriceCheck, RivenCatalog, RivenDraft, RivenService, RivenWeapon
from .widgets import BUTTON_STYLE, guard_wheel
from .riven_find import FindPanel, weapon_title
from .riven_price import PricePanel

log = logging.getLogger(__name__)


class RivenWindow(QWidget):
    _async_done = pyqtSignal(object, object)  # (callback, result) run on the UI thread

    def __init__(self, catalog: RivenCatalog, service: RivenService, account: Optional[Account] = None) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace rivens")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        theme.follow(self, STYLE + BUTTON_STYLE)
        self.resize(980, 680)
        self.catalog = catalog
        self.service = service
        self.account = account if account is not None else Account(service.client)
        self.weapon: Optional[RivenWeapon] = None
        self._executor = ThreadPoolExecutor(max_workers=2)

        self.search = QLineEdit(placeholderText="Search a weapon, e.g. kulstar")
        self.account_button = QPushButton(objectName="account")
        self.account_button.clicked.connect(self._account_clicked)
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(180)
        self.suggestions.hide()
        self.title = QLabel("Pick a weapon to see its rivens.", objectName="title")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.toast = QLabel(objectName="toast")
        self._toast_timer = QTimer(self, singleShot=True, interval=4000)
        self._toast_timer.timeout.connect(lambda: self.toast.setText(""))

        from .riven_trade import MyRivensPanel, SellPanel  # imported here: it imports this module's types

        self.find_panel = FindPanel(self)
        self.price_panel = PricePanel(self)
        self.sell_panel = SellPanel(self)
        self.my_rivens = MyRivensPanel(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.find_panel, "Find rivens")
        self.tabs.addTab(self.price_panel, "Price check")
        self.tabs.addTab(self.sell_panel, "Sell")
        self.tabs.addTab(self.my_rivens, "My rivens")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.price_panel.sell_requested.connect(self.open_sell)

        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.account_button)
        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.suggestions)
        layout.addWidget(self.title)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.toast)
        self._async_done.connect(lambda callback, result: callback(result))
        self.account_changed()
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
        self.tabs.setCurrentWidget(self.price_panel)

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
        self.tabs.setCurrentWidget(self.sell_panel)

    # --- account --------------------------------------------------------------------------

    def account_changed(self) -> None:
        """Called after signing in or out, here or in the market window (they share one account)."""
        if self.account.signed_in:
            self.account_button.setText(f"Signed in as {self.account.ingame_name or 'you'}  \u00b7  Sign out")
        else:
            self.account_button.setText("Sign in to Warframe.Market")
        self.sell_panel.update_state()
        if self.tabs.currentWidget() is self.my_rivens:
            self.my_rivens.refresh()

    def show_sign_in(self) -> None:
        from .orders import SignInDialog

        SignInDialog(self).exec()

    def _account_clicked(self) -> None:
        if self.account.signed_in:
            self.account.sign_out()
            self.account_changed()
        else:
            self.show_sign_in()

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.my_rivens:
            self.my_rivens.refresh()
        elif self.tabs.widget(index) is self.sell_panel:
            self.sell_panel.update_state()

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

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.account_changed()  # you may have signed in or out in the market window meanwhile

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()  # closing only hides it; the app keeps running in the tray
        self.hide()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
