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
from rapidfuzz import fuzz, process, utils

from . import theme
from .account import Account
from .icons import platinum_icon
from .items import CatalogItem
from .sellers import SellerOrder, SellerService, rank_counts, whisper_text

log = logging.getLogger(__name__)

STYLE = """
QWidget { background: #0b1220; color: #e6eefb; font-family: 'Segoe UI'; font-size: 13px; }
QLineEdit, QComboBox { background: #111a2e; border: 1px solid #22314f; border-radius: 4px; padding: 5px 8px; }
QLineEdit:focus { border-color: #4fa3ff; }
QListWidget { background: #111a2e; border: 1px solid #22314f; }
QListWidget::item:selected { background: #22314f; }
QTableWidget { background: #0b1220; border: none; gridline-color: #16213a; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:hover { background: #16213a; }
QHeaderView::section { background: #0b1220; color: #8fa3c2; border: none; border-bottom: 1px solid #22314f;
                       padding: 6px; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 18px; font-weight: 600; }
QLabel#toast { color: #7cc99c; }
QLabel#muted { color: #8fa3c2; }
QTabWidget::pane { border: none; border-top: 1px solid #22314f; }
QTabBar::tab { background: #0b1220; color: #8fa3c2; padding: 7px 14px; border: none; }
QTabBar::tab:selected { color: #e6eefb; border-bottom: 2px solid #4fa3ff; }
QPushButton#account { background: #111a2e; border: 1px solid #22314f; border-radius: 4px; padding: 5px 10px; }
"""

COLUMNS = ["Seller", "Status", "Rank", "Price", "Qty", "Rep"]
STATUS_LABEL = {"ingame": "In game", "online": "Online"}
STATUS_COLOR = {"ingame": QColor(124, 201, 156), "online": QColor(120, 185, 255)}
MAX_SUGGESTIONS = 8


# Short forms players type in trade chat.
TYPO_MIN = 70  # per-word similarity needed to count as a typo of that word
ALIASES = {"bp": "blueprint", "sys": "systems", "neuro": "neuroptics", "chas": "chassis", "rec": "receiver"}


def _words(text: str) -> list[str]:
    return utils.default_process(text).split()


def search_catalog(query: str, catalog: Sequence[CatalogItem], limit: int = MAX_SUGGESTIONS) -> list[CatalogItem]:
    """Shop-style search, best tier first:
        0  name starts with the query               "prim"        -> Primed ...
        1  every query word starts a name word, in order   "arcane ener" -> Arcane Energize
        2  every query word starts a name word, any order
        3  query appears inside the name
        4  only when nothing above matched: each word within a typo of a name word ("nekrso" -> Nekros)
    Ties go to the shorter name ("Serration" before "Spectral Serration"), typos to the closer match.
    WRatio alone ranked hundreds of names equally and fell back to alphabetical order.
    """
    q_words = [ALIASES.get(w, w) for w in _words(query)]
    if not q_words or len(" ".join(q_words)) < 2:
        return []
    q = " ".join(q_words)
    scored = []
    for item in catalog:
        name = " ".join(_words(item.name))
        n_words = name.split()
        if name.startswith(q):
            tier = 0
        elif _prefix_in_order(q_words, n_words):
            tier = 1
        elif all(any(w.startswith(qw) for w in n_words) for qw in q_words):
            tier = 2
        elif q in name:
            tier = 3
        else:
            continue
        scored.append((tier, 0.0, len(name), name, item))
    if not scored:
        # Typos only: match each typed word to its closest name word (or the start of one).
        for item in catalog:
            n_words = _words(item.name)
            best = [max(max(fuzz.ratio(qw, w), fuzz.ratio(qw, w[:len(qw) + 1])) for w in n_words) for qw in q_words]
            if min(best) >= TYPO_MIN:
                name = " ".join(n_words)
                scored.append((4, -round(sum(best) / len(best)), len(name), name, item))
    scored.sort(key=lambda s: s[:4])
    return [s[4] for s in scored[:limit]]


def _prefix_in_order(q_words: list[str], n_words: list[str]) -> bool:
    i = 0
    for w in n_words:
        if i < len(q_words) and w.startswith(q_words[i]):
            i += 1
    return i == len(q_words)


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

        from .trade_panel import MyOrdersPanel, PostOrderPanel  # imported here: it imports this module's types

        self.account_button = QPushButton(objectName="account")
        self.account_button.clicked.connect(self._account_clicked)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.account_button)

        controls = QHBoxLayout()
        controls.addWidget(self.title, 1)
        controls.addWidget(self.rank)
        controls.addWidget(self.online)
        buy_tab = QWidget()
        buy_layout = QVBoxLayout(buy_tab)
        buy_layout.setContentsMargins(0, 8, 0, 0)
        buy_layout.addLayout(controls)
        buy_layout.addWidget(QLabel("Click a seller to copy the whisper, then paste it in game chat.", objectName="muted"))
        buy_layout.addWidget(self.table, 1)
        buy_layout.addWidget(self.status)

        self.post_panel = PostOrderPanel(self)
        self.orders_panel = MyOrdersPanel(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(buy_tab, "Buy from players")
        self.tabs.addTab(self.post_panel, "Post order")
        self.tabs.addTab(self.orders_panel, "My orders")
        self.tabs.currentChanged.connect(self._tab_changed)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.suggestions)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.toast)
        self._async_done.connect(lambda callback, result: callback(result))
        self.account_changed()
        from .trade_panel import guard_wheel

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
        from .trade_panel import SignInDialog

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

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing only hides it; the app keeps running in the tray.
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
