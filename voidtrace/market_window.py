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
from .sellers import SellerOrder, SellerService, whisper_text

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


def search_catalog(query: str, catalog: Sequence[CatalogItem], limit: int = MAX_SUGGESTIONS) -> list[CatalogItem]:
    """Fuzzy item search for typed queries ("primed cont", "burston bp")."""
    query = query.strip()
    if len(query) < 2:
        return []
    names = [item.name for item in catalog]
    hits = process.extract(query, names, scorer=fuzz.WRatio, processor=utils.default_process,
                           limit=limit, score_cutoff=60)
    return [catalog[index] for _, _, index in hits]


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
            for r in range(item.max_rank + 1):
                self.rank.addItem(f"Rank {r}", r)
            self.rank.setCurrentIndex(item.max_rank if rank is None else rank)
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
