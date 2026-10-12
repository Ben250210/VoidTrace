"""The VoidTrace window: a sidebar on the left (Market, Rivens, Relics and their pages) and the chosen
page on the right. The account button and the accent colour sit at the bottom of the sidebar.

Every page is addressed by a key such as "market/buy" or "rivens/price". The last page you used is
remembered (settings `last_page`), and so is the window size (`window_size`). Riven data is a second
download, so the Rivens section is built the first time one of its pages is opened.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import (QColorDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget, QToolButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import settings, theme
from .market import MarketSection
from .relics import RelicSection
from .widgets import BUTTON_STYLE, STYLE, Section

log = logging.getLogger(__name__)

RIVEN_PAGES = {"find": "Find", "price": "Price check", "sell": "Sell", "mine": "My rivens"}
SIDEBAR = (("market", "MARKET", MarketSection.PAGES),
           ("rivens", "RIVENS", RIVEN_PAGES),
           ("relics", "RELICS", RelicSection.PAGES))
PAGE_KEYS = [f"{section}/{page}" for section, _title, pages in SIDEBAR for page in pages]
DEFAULT_PAGE = "market/buy"
DEFAULT_SIZE = (1100, 720)

SIDEBAR_STYLE = """
QTreeWidget#sidebar { background: #08101c; border: none; border-right: 1px solid #16213a; font-size: 13px; }
QTreeWidget#sidebar::item { padding: 5px 6px; border: none; }
QTreeWidget#sidebar::item:selected { background: #16213a; color: #4fa3ff; border-left: 2px solid #4fa3ff; }
QTreeWidget#sidebar::item:hover:!selected { background: #0f192b; }
QLabel#brand { font-size: 16px; font-weight: 700; color: #4fa3ff; padding: 8px 6px 2px 6px; }
QPushButton#account, QToolButton#accent { background: #111a2e; border: 1px solid #22314f; border-radius: 4px;
    padding: 5px 8px; text-align: left; }
QToolButton#accent::menu-indicator { image: none; }
"""


def accent_menu(parent: QWidget) -> QMenu:
    """Accent colour: presets, or any colour; remembered, and every window follows at once."""
    menu = QMenu("Accent colour", parent)
    group = QActionGroup(menu)
    current = theme.accent_hex().lower()
    for name, color in theme.PRESETS.items():
        action = menu.addAction(name)
        action.setCheckable(True)
        action.setChecked(color.lower() == current)
        action.setActionGroup(group)
        action.triggered.connect(lambda _=False, c=color: theme.set_accent(c))
    menu.addSeparator()

    def pick() -> None:
        chosen = QColorDialog.getColor(theme.accent(), None, "VoidTrace accent colour")
        if chosen.isValid():
            for action in group.actions():
                action.setChecked(False)
            theme.set_accent(chosen.name())

    menu.addAction("Custom…", pick)
    return menu


class MainWindow(QWidget):
    _rivens_loaded = pyqtSignal(object)
    _rivens_failed = pyqtSignal(str)

    def __init__(self, market: MarketSection, relics: RelicSection,
                 load_riven_data: Callable[[], object], make_rivens: Callable[[object], Section]) -> None:
        super().__init__()
        self.setWindowTitle("VoidTrace")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        theme.follow(self, STYLE + BUTTON_STYLE + SIDEBAR_STYLE)
        self.market, self.relics = market, relics
        self.account = market.account
        self.load_riven_data, self.make_rivens = load_riven_data, make_rivens
        self._rivens: Optional[Section] = None
        self._rivens_loading = False
        self._riven_page = "find"
        self._riven_waiting: list[Callable[[Section], None]] = []
        self._current = ""

        self.sidebar = QTreeWidget(objectName="sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIndentation(10)
        self._items: dict[str, QTreeWidgetItem] = {}
        for section, title, pages in SIDEBAR:
            header = QTreeWidgetItem([title])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = header.font(0)
            font.setBold(True)
            font.setPointSize(max(7, font.pointSize() - 2))
            header.setFont(0, font)
            header.setForeground(0, Qt.GlobalColor.gray)
            self.sidebar.addTopLevelItem(header)
            for page, label in pages.items():
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, f"{section}/{page}")
                header.addChild(item)
                self._items[f"{section}/{page}"] = item
        self.sidebar.expandAll()
        self.sidebar.itemClicked.connect(self._clicked)

        self.account_button = QPushButton(objectName="account")
        self.account_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.account_button.clicked.connect(self._account_clicked)
        self.accent_button = QToolButton(objectName="accent", text="Accent colour")
        self.accent_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.accent_button.setMenu(accent_menu(self))
        self.accent_button.setCursor(Qt.CursorShape.PointingHandCursor)

        self.riven_placeholder = QLabel(objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.stack = QStackedWidget()
        for widget in (market, relics, self.riven_placeholder):
            self.stack.addWidget(widget)
        for section in (market, relics):
            self._watch(section)

        side = QWidget()
        side.setFixedWidth(190)
        left = QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 8)
        left.addWidget(QLabel("VoidTrace", objectName="brand"))
        left.addWidget(self.sidebar, 1)
        left.addWidget(self.account_button)
        left.addWidget(self.accent_button)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 10, 10)
        layout.addWidget(side)
        layout.addWidget(self.stack, 1)

        self._rivens_loaded.connect(self._on_rivens_loaded)
        self._rivens_failed.connect(self._on_rivens_failed)
        self._save_size = QTimer(self, singleShot=True, interval=500)
        self._save_size.timeout.connect(lambda: settings.put("window_size", [self.width(), self.height()]))
        size = settings.get("window_size")
        try:
            self.resize(int(size[0]), int(size[1]))
        except (TypeError, ValueError, IndexError):
            self.resize(*DEFAULT_SIZE)
        self.refresh_account_button()
        last = settings.get("last_page")
        self.show_page(last if last in PAGE_KEYS else DEFAULT_PAGE)

    # --- pages ------------------------------------------------------------------------------------

    def section(self, name: str) -> Optional[Section]:
        return {"market": self.market, "relics": self.relics, "rivens": self._rivens}.get(name)

    def rivens(self) -> Optional[Section]:
        return self._rivens

    def current_page(self) -> str:
        return self._current

    def sidebar_keys(self) -> list[str]:
        return list(self._items)

    def sidebar_labels(self) -> list[str]:
        return [item.text(0) for item in self._items.values()]

    def show_page(self, key: str) -> None:
        if key not in PAGE_KEYS:
            key = DEFAULT_PAGE
        name, page = key.split("/")
        self._current = key
        settings.put("last_page", key)
        self._select(key)
        if name == "rivens" and self._rivens is None:
            self._riven_page = page
            self.stack.setCurrentWidget(self.riven_placeholder)
            self._load_rivens()
            return
        section = self.section(name)
        self.stack.setCurrentWidget(section)
        section.show_page(page)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key:
            self.show_page(key)

    def _select(self, key: str) -> None:
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentItem(self._items[key])
        self.sidebar.blockSignals(False)

    def _watch(self, section: Section) -> None:
        name = next(n for n in ("market", "relics", "rivens") if self.section(n) is section)
        section.account_switched.connect(self.account_changed)
        section.page_changed.connect(lambda page, n=name: self._section_moved(f"{n}/{page}"))

    def _section_moved(self, key: str) -> None:
        """A section switched page by itself (e.g. Price check -> Sell): keep the sidebar in step."""
        if key in PAGE_KEYS and key != self._current and self.stack.currentWidget() is self.section(key.split("/")[0]):
            self._current = key
            settings.put("last_page", key)
            self._select(key)

    # --- rivens, loaded on first use -----------------------------------------------------------------

    def with_rivens(self, callback: Callable[[Section], None]) -> None:
        """Call `callback(rivens_section)` now, or once riven data has loaded (it starts loading)."""
        if self._rivens is not None:
            callback(self._rivens)
            return
        self._riven_waiting.append(callback)
        self._load_rivens()

    def _load_rivens(self) -> None:
        if self._rivens_loading:
            return
        self._rivens_loading = True
        self.riven_placeholder.setText("Loading riven data…")

        def work() -> None:
            try:
                self._rivens_loaded.emit(self.load_riven_data())
            except Exception as exc:
                log.warning("could not load riven data: %s", exc)
                self._rivens_failed.emit(str(exc))

        threading.Thread(target=work, name="VoidTrace-RivenData", daemon=True).start()

    def _on_rivens_loaded(self, data) -> None:
        self._rivens_loading = False
        try:
            self._rivens = self.make_rivens(data)
        except Exception as exc:
            log.warning("could not build the Rivens section: %s", exc)
            self._on_rivens_failed(str(exc))
            return
        self.stack.addWidget(self._rivens)
        self._watch(self._rivens)
        if self._current.startswith("rivens/"):
            self.show_page(f"rivens/{self._riven_page}")
        waiting, self._riven_waiting = self._riven_waiting, []
        for callback in waiting:
            callback(self._rivens)

    def _on_rivens_failed(self, message: str) -> None:
        self._rivens_loading = False
        self._riven_waiting.clear()
        self.riven_placeholder.setText(f"Could not load riven data from Warframe.Market: {message}\n\n"
                                       "Open a Rivens page again to retry.")

    # --- account -----------------------------------------------------------------------------------

    def refresh_account_button(self) -> None:
        if self.account.signed_in:
            self.account_button.setText(f"Signed in as {self.account.ingame_name or 'you'} · Sign out")
        else:
            self.account_button.setText("Sign in to Warframe.Market")

    def account_changed(self) -> None:
        """Signed in or out somewhere: update the button and every other section."""
        origin = self.sender()
        self.refresh_account_button()
        for name in ("market", "rivens", "relics"):
            section = self.section(name)
            if section is not None and section is not origin:
                section.refresh_account()

    def _account_clicked(self) -> None:
        if self.account.signed_in:
            self.account.sign_out()
            self.account_changed()
        else:
            self.market.show_sign_in()

    # --- window ------------------------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._save_size.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()  # closing only hides it; VoidTrace keeps running in the tray
        self.hide()

    def shutdown(self) -> None:
        for name in ("market", "rivens", "relics"):
            section = self.section(name)
            if section is not None:
                section.shutdown()
