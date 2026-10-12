"""Market window: search any item, see who is selling it right now, click a seller to copy the whisper,
and (signed in) post and manage your own Warframe.Market orders.

A normal clickable window (unlike the overlay), kept on top of Borderless Fullscreen Warframe.
"""

from __future__ import annotations

import html

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..account import Account
from ..icons import platinum_icon
from ..items import CatalogItem
from ..sellers import SellerOrder, SellerService, rank_counts, whisper_text
from ..search import search_catalog
from .widgets import STATUS_COLOR, STATUS_LABEL, STYLE

log = logging.getLogger(__name__)


COLUMNS = ["Seller", "Status", "Rank", "Price", "Qty", "Rep"]


def item_title(item: CatalogItem) -> str:
    """Item name, with its max rank in smaller grey text for ranked items (mods, arcanes)."""
    name = html.escape(item.name)
    if item.max_rank is None:
        return name
    return f'{name} <span style="color:#8fa3c2; font-size:13px; font-weight:400">max rank {item.max_rank}</span>'


class MarketWindow(QWidget):
    _sellers_ready = pyqtSignal(int, list)
    _async_done = pyqtSignal(object, object)  # (callback, result) run on the UI thread
    _sellers_failed = pyqtSignal(int, str)
    alerts_changed = pyqtSignal()

    def __init__(self, catalog: Sequence[CatalogItem], sellers: SellerService,
                 account: Optional[Account] = None) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace market")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        theme.follow(self, STYLE)
        self.resize(640, 560)
        self.catalog = list(catalog)
        self.by_slug = {item.slug: item for item in self.catalog}
        self.by_id = {item.id: item for item in self.catalog if item.id}
        self.account = account if account is not None else Account(sellers.client)
        self.sellers = sellers
        self.item: Optional[CatalogItem] = None
        self.rows: list[SellerOrder] = []
        self._request = 0
        self._executor = ThreadPoolExecutor(max_workers=2)

        self.search = QLineEdit(placeholderText="Search an item, e.g. primed continuity")
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(180)
        self.suggestions.hide()
        self.title = QLabel(objectName="title")
        self.rank = QComboBox()
        # Grow with its labels: they gain seller counts after loading ("Rank 10 (53)"), and a
        # fixed width cut them to "Ran...(0)".
        self.rank.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.rank.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.online = QCheckBox("Include online sellers")
        self.online.setChecked(True)
        self.alert_button = QPushButton("Alert me\u2026")
        self.alert_button.setToolTip("Tell me when someone sells this at or under a price")
        self.alert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alert_button.clicked.connect(self._ask_alert)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeaderItem(3).setIcon(QIcon(platinum_icon(14)))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(14, 14))
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.status = QLabel(objectName="muted")
        self.toast = QLabel(objectName="toast")
        self._toast_timer = QTimer(self, singleShot=True, interval=4000)
        self._toast_timer.timeout.connect(lambda: self.toast.setText(""))

        from .orders import MyOrdersPanel, PostOrderPanel  # imported here: it imports this module's types

        self.account_button = QPushButton(objectName="account")
        self.account_button.clicked.connect(self._account_clicked)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.account_button)

        controls = QHBoxLayout()
        controls.addWidget(self.title, 1)
        controls.addWidget(self.rank)
        controls.addWidget(self.online)
        controls.addWidget(self.alert_button)
        buy_tab = QWidget()
        buy_layout = QVBoxLayout(buy_tab)
        buy_layout.setContentsMargins(0, 8, 0, 0)
        buy_layout.addLayout(controls)
        buy_layout.addWidget(QLabel("Click a seller to copy the whisper, then paste it in game chat.", objectName="muted"))
        buy_layout.addWidget(self.table, 1)
        buy_layout.addWidget(self.status)

        self.post_panel = PostOrderPanel(self)
        self.orders_panel = MyOrdersPanel(self)
        from .trades import TradeHistoryPanel

        self.trades_panel = TradeHistoryPanel()
        from .alerts import AlertsPanel

        self.alerts_panel = AlertsPanel(on_changed=self.alerts_changed.emit)
        self.tabs = QTabWidget()
        self.tabs.addTab(buy_tab, "Buy from players")
        self.tabs.addTab(self.post_panel, "Post order")
        self.tabs.addTab(self.orders_panel, "My orders")
        self.tabs.addTab(self.trades_panel, "Trades")
        self.tabs.addTab(self.alerts_panel, "Alerts")
        self.tabs.currentChanged.connect(self._tab_changed)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.suggestions)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.toast)
        self._async_done.connect(lambda callback, result: callback(result))
        self.account_changed()
        from .widgets import guard_wheel

        self._wheel_guard = guard_wheel(self)  # scrolling over the rank picker changed the rank

        self.search.textEdited.connect(self._on_search)
        self.search.returnPressed.connect(self._pick_first_suggestion)
        self.suggestions.itemClicked.connect(lambda it: self.open_item(it.data(Qt.ItemDataRole.UserRole)))
        self.rank.currentIndexChanged.connect(lambda _: self.refresh())
        self.online.toggled.connect(lambda _: self.refresh())
        self.table.cellClicked.connect(lambda row, _col: self.copy_row(row))
        self._sellers_ready.connect(self._on_sellers)
        self._sellers_failed.connect(self._on_failed)

    # --- search -----------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self.suggestions.clear()
        hits = search_catalog(text, self.catalog)
        for item in hits:
            self.suggestions.addItem(item.name)
            self.suggestions.item(self.suggestions.count() - 1).setData(Qt.ItemDataRole.UserRole, item.slug)
        self.suggestions.setVisible(bool(hits))

    def _pick_first_suggestion(self) -> None:
        if self.suggestions.count():
            self.open_item(self.suggestions.item(0).data(Qt.ItemDataRole.UserRole))

    # --- item + sellers -----------------------------------------------------------

    def open_item(self, slug: str, rank: Optional[int] = None) -> None:
        item = self.by_slug.get(slug)
        if item is None:
            log.warning("unknown item %s", slug)
            return
        self.item = item
        self.suggestions.hide()
        self.search.setText(item.name)
        self.title.setText(item_title(item))
        self.rank.blockSignals(True)
        self.rank.clear()
        if item.max_rank is not None:
            # "Any rank" first, like warframe.market: most mods are sold at rank 0 or max rank, so a
            # single fixed rank hid most of the market.
            self.rank.addItem("Any rank", None)
            for r in range(item.max_rank + 1):
                self.rank.addItem(f"Rank {r}", r)
            self.rank.setCurrentIndex(0 if rank is None else rank + 1)
        self.rank.setVisible(item.max_rank is not None)
        self.rank.blockSignals(False)
        self.post_panel.set_item(rank)
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def selected_rank(self) -> Optional[int]:
        return self.rank.currentData() if self.item and self.item.max_rank is not None else None

    def refresh(self) -> None:
        if self.item is None:
            return
        self._request += 1
        request, slug, rank, online = self._request, self.item.slug, self.selected_rank(), self.online.isChecked()
        self.table.setRowCount(0)
        self.status.setText("Loading sellers…")

        def work():
            try:
                self._sellers_ready.emit(request, self.sellers.sellers(slug, rank, online))
            except Exception as exc:  # network trouble must not kill the UI
                log.warning("seller fetch failed for %s: %s", slug, exc)
                self._sellers_failed.emit(request, str(exc))

        self._executor.submit(work)

    def _on_failed(self, request: int, message: str) -> None:
        if request == self._request:
            self.status.setText("Could not reach Warframe.Market. Try again in a moment.")

    def _on_sellers(self, request: int, rows: list) -> None:
        if request != self._request:
            return  # an older request finished after the user moved on
        if self.item is not None and self.item.max_rank is not None and self.selected_rank() is None:
            self._label_ranks(rank_counts(rows))
        self.rows = rows
        self.table.setRowCount(len(rows))
        dim = QColor(104, 118, 145)
        for r, s in enumerate(rows):
            cells = [s.ingame_name, STATUS_LABEL.get(s.status, s.status), "" if s.rank is None else str(s.rank),
                     str(s.platinum), str(s.quantity), str(s.reputation)]
            for c, value in enumerate(cells):
                cell = QTableWidgetItem(value)
                if c >= 2:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 1:
                    cell.setForeground(STATUS_COLOR.get(s.status, dim))
                if c == 3:
                    cell.setIcon(QIcon(platinum_icon(14)))
                if s.suspicious:
                    cell.setForeground(dim)
                    cell.setToolTip("Price is far from the other sellers: likely a troll listing or a typo.")
                else:
                    cell.setToolTip("Click to copy the whisper")
                self.table.setItem(r, c, cell)
        if self.item is not None and self.item.max_rank is None:
            self.table.hideColumn(2)
        else:
            self.table.showColumn(2)
        n_flag = sum(s.suspicious for s in rows)
        self.status.setText(f"{len(rows)} sellers" + (f", {n_flag} with unusual prices (greyed out)" if n_flag else "")
                            if rows else "Nobody is selling this right now.")

    def _label_ranks(self, counts: dict) -> None:
        """Show how many sellers each rank has, e.g. "Rank 0 (52)"."""
        self.rank.blockSignals(True)
        for i in range(1, self.rank.count()):
            r = self.rank.itemData(i)
            self.rank.setItemText(i, f"Rank {r} ({counts.get(r, 0)})")
        self.rank.setItemText(0, f"Any rank ({sum(counts.values())})")
        view = self.rank.view()
        view.setMinimumWidth(view.sizeHintForColumn(0) + 24)
        self.rank.blockSignals(False)

    # --- price alerts ------------------------------------------------------------------

    def suggested_alert_price(self) -> int:
        """One under the cheapest normal listing on show, so the alert means "cheaper than now"."""
        prices = [s.platinum for s in self.rows if not s.suspicious]
        return max(1, min(prices) - 1) if prices else 1

    def _ask_alert(self) -> None:
        if self.item is None:
            return
        rank = self.selected_rank()
        label = self.item.name + (f" (rank {rank})" if rank is not None else "")
        price, ok = QInputDialog.getInt(self, "Price alert",
                                        f"Tell me when someone sells {label}\nfor this many platinum or less:",
                                        self.suggested_alert_price(), 1, 100_000)
        if ok:
            self.add_alert(price)

    def add_alert(self, price: int) -> None:
        from ..alerts import Alert, add_alert

        rank = self.selected_rank()
        alert = Alert(self.item.slug, self.item.name, int(price), rank, self.item.max_rank is not None)
        add_alert(alert)
        self.alerts_panel.refresh()
        self.alerts_changed.emit()
        self.toast.setText(f"Alert set: {alert.label} at {price}p or less. Checked every minute.")
        self._toast_timer.start()

    # --- clipboard ---------------------------------------------------------------------

    def whisper_for(self, row: int) -> str:
        return whisper_text(self.rows[row], self.item.name, ranked=self.item.max_rank is not None)

    def copy_row(self, row: int) -> None:
        if not (0 <= row < len(self.rows)) or self.item is None:
            return
        QApplication.clipboard().setText(self.whisper_for(row))
        self.toast.setText(f"Copied whisper to {self.rows[row].ingame_name}. Paste it in game chat.")
        self._toast_timer.start()

    # --- account + background work -----------------------------------------------------

    def run_async(self, fn, on_ok, on_err=None) -> None:
        """Run `fn` off the UI thread; call on_ok(result) or on_err(exception) back on the UI thread."""
        def work():
            try:
                self._async_done.emit(on_ok, fn())
            except Exception as exc:  # reported to the panel that asked, never crashes the UI
                log.warning("market request failed: %s", exc)
                if on_err is not None:
                    self._async_done.emit(on_err, exc)

        self._executor.submit(work)

    def account_changed(self) -> None:
        if self.account.signed_in:
            self.account_button.setText(f"Signed in as {self.account.ingame_name or 'you'}  \u00b7  Sign out")
        else:
            self.account_button.setText("Sign in to Warframe.Market")
        self.post_panel.update_price()
        if self.tabs.currentWidget() is self.orders_panel:
            self.orders_panel.refresh()

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
        if self.tabs.widget(index) is self.orders_panel:
            self.orders_panel.refresh()
        elif self.tabs.widget(index) is self.trades_panel:
            self.trades_panel.refresh()
        elif self.tabs.widget(index) is self.alerts_panel:
            self.alerts_panel.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing only hides it; the app keeps running in the tray.
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
