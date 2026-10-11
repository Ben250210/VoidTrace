"""Market window: search any item, see who is selling it right now, click a seller to copy the whisper.

A normal clickable window (unlike the overlay), kept on top of Borderless Fullscreen Warframe.
"""

from __future__ import annotations

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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from rapidfuzz import fuzz, process, utils

from .icons import platinum_icon
from .items import CatalogItem
from .sellers import SellerOrder, SellerService, rank_counts, whisper_text

log = logging.getLogger(__name__)

STYLE = """
QWidget { background: #0f1119; color: #e7e5dc; font-family: 'Segoe UI'; font-size: 13px; }
QLineEdit, QComboBox { background: #171a26; border: 1px solid #2a2f45; border-radius: 4px; padding: 5px 8px; }
QLineEdit:focus { border-color: #d9b45c; }
QListWidget { background: #171a26; border: 1px solid #2a2f45; }
QListWidget::item:selected { background: #2a2f45; }
QTableWidget { background: #0f1119; border: none; gridline-color: #1d2131; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:hover { background: #1d2131; }
QHeaderView::section { background: #0f1119; color: #9ba0b5; border: none; border-bottom: 1px solid #2a2f45;
                       padding: 6px; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 18px; font-weight: 600; }
QLabel#toast { color: #7cc99c; }
QLabel#muted { color: #9ba0b5; }
"""

COLUMNS = ["Seller", "Status", "Rank", "Price", "Qty", "Rep"]
STATUS_LABEL = {"ingame": "In game", "online": "Online"}
STATUS_COLOR = {"ingame": QColor(124, 201, 156), "online": QColor(143, 163, 255)}
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


class MarketWindow(QWidget):
    _sellers_ready = pyqtSignal(int, list)
    _sellers_failed = pyqtSignal(int, str)

    def __init__(self, catalog: Sequence[CatalogItem], sellers: SellerService) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace market")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        self.resize(640, 560)
        self.catalog = list(catalog)
        self.by_slug = {item.slug: item for item in self.catalog}
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

        controls = QHBoxLayout()
        controls.addWidget(self.title, 1)
        controls.addWidget(self.rank)
        controls.addWidget(self.online)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.suggestions)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Click a seller to copy the whisper, then paste it in game chat.", objectName="muted"))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.toast)

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
            label = item.name + (f"  (max rank {item.max_rank})" if item.max_rank else "")
            self.suggestions.addItem(label)
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
        self.title.setText(item.name)
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
        dim = QColor(110, 116, 135)
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

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing only hides it; the app keeps running in the tray.
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
