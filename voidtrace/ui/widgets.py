"""Shared look and building blocks for every VoidTrace page: the stylesheets, table and cell helpers,
the number box with -/+ buttons, the mouse-wheel guard, the colours used across pages, and `Section`,
the base of the Market, Rivens and Relics sections of the main window."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

log = logging.getLogger(__name__)

STYLE = """
QWidget { background: #0b1220; color: #e6eefb; font-family: 'Segoe UI'; font-size: 13px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #111a2e; border: 1px solid #22314f;
    border-radius: 6px; padding: 6px 10px; min-height: 20px; selection-background-color: #4fa3ff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #4fa3ff; }
QLineEdit:hover, QComboBox:hover { border-color: #33476e; }
QComboBox QAbstractItemView { background: #111a2e; border: 1px solid #22314f; selection-background-color: #1c2a47; }
QListWidget { background: #0e1729; border: 1px solid #1b2742; border-radius: 8px; padding: 4px; outline: none; }
QListWidget::item { padding: 7px 10px; border-radius: 5px; }
QListWidget::item:selected { background: #1c2a47; color: #e6eefb; }
QListWidget::item:hover:!selected { background: #142038; }
QTableWidget { background: #0b1220; alternate-background-color: #0e1729; border: 1px solid #16213a;
    border-radius: 8px; gridline-color: transparent; selection-background-color: #1c2a47; outline: none; }
QTableWidget::item { padding: 4px 8px; border: none; }
QTableWidget::item:hover { background: #16213a; }
QTableCornerButton::section { background: #0e1729; border: none; }
QHeaderView::section { background: #0e1729; color: #8fa3c2; border: none; border-bottom: 1px solid #22314f;
    padding: 9px 8px; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 18px; font-weight: 600; }
QLabel#toast { color: #7cc99c; }
QLabel#muted { color: #8fa3c2; }
QCheckBox { spacing: 8px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 4px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #22314f; border-radius: 3px; min-height: 30px;
    min-width: 30px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #33476e; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""

BUTTON_STYLE = """
QPushButton { background: #22314f; border: 1px solid #33476e; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background: #2b3d62; }
QPushButton#primary { background: #4fa3ff; color: #06101f; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #74b8ff; }
QPushButton#danger { color: #f08a6e; padding: 3px 10px; }
QPushButton#small { padding: 3px 10px; }
QToolButton#eye { background: transparent; border: none; padding: 2px; }
QToolButton#eye:hover { background: #16213a; border-radius: 4px; }
QSpinBox { background: #111a2e; border: 1px solid #22314f; border-radius: 4px; padding: 4px 6px; }
QToolButton#step { background: #22314f; border: 1px solid #33476e; border-radius: 4px; font-weight: 700;
                   min-width: 26px; min-height: 24px; }
QToolButton#step:hover { background: #33476e; }
QToolButton#step:pressed { background: #4fa3ff; color: #06101f; }
QRadioButton::indicator, QCheckBox::indicator { width: 14px; height: 14px; }
QRadioButton::indicator { border-radius: 8px; border: 2px solid #5b7099; background: #0b1220; }
QRadioButton::indicator:hover { border-color: #4fa3ff; }
QRadioButton::indicator:checked { border: 2px solid #4fa3ff;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 #4fa3ff, stop:0.5 #4fa3ff, stop:0.6 #0b1220, stop:1 #0b1220); }
"""

MUTED = QColor(143, 163, 194)
GREEN = QColor("#5ccf8f")
RED = QColor("#ff6b6b")
BUFF_COLOR = QColor(124, 201, 156)
CURSE_COLOR = QColor(240, 110, 90)
STATUS_LABEL = {"ingame": "In game", "online": "Online", "offline": "Offline"}
STATUS_COLOR = {"ingame": QColor(124, 201, 156), "online": QColor(120, 185, 255), "offline": QColor(143, 163, 194)}


class WheelGuard(QObject):
    """Dropdowns and number boxes ignore the mouse wheel until clicked.

    Qt changes a hovered dropdown on every wheel notch, so scrolling the window over the price
    check silently turned Puncture into the next stat in the list, Radiation.
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()  # lets the window scroll instead
            return True
        return False


def guard_wheel(root: QWidget) -> WheelGuard:
    guard = WheelGuard(root)
    for widget in root.findChildren((QComboBox, QAbstractSpinBox)):
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        widget.installEventFilter(guard)
    return guard


class Stepper(QWidget):
    """A number box with its own - and + buttons.

    The dark theme's QSpinBox styling made Qt misplace the built-in arrows' click areas: the up
    arrow drew in one place and listened in another, so it never responded. Typing and the mouse
    wheel still work on the box itself. Mirrors QSpinBox's value()/setValue()/valueChanged.
    """

    def __init__(self, minimum: int, maximum: int, value: int, step: int = 1) -> None:
        super().__init__()
        self.box = QSpinBox(minimum=minimum, maximum=maximum, value=value)
        self.box.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box.setMinimumWidth(64)
        self.minus = QToolButton(text="\u2212", objectName="step")
        self.plus = QToolButton(text="+", objectName="step")
        for button, delta in ((self.minus, -step), (self.plus, step)):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoRepeat(True)  # hold to keep stepping
            button.clicked.connect(lambda _=False, d=delta: self.box.setValue(self.box.value() + d))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.minus)
        layout.addWidget(self.box)
        layout.addWidget(self.plus)
        self.valueChanged = self.box.valueChanged

    def value(self) -> int:
        return self.box.value()

    def setValue(self, value: int) -> None:  # noqa: N802 (Qt naming)
        self.box.setValue(value)


def make_table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setShowGrid(False)
    t.setIconSize(QSize(14, 14))
    t.setAlternatingRowColors(True)  # striped rows: easier to follow a row across wide tables
    t.verticalHeader().setDefaultSectionSize(36)
    header = t.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, len(columns)):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    return t


def set_rows(table: QTableWidget, count: int) -> None:
    """Size `table` for a refill. Emptying it first matters: refilling rows that still hold text made
    the fit-to-contents columns re-measure every row for each new cell (752 relics froze the window)."""
    table.setRowCount(0)
    table.setRowCount(count)


def make_cell(text: str, right: bool = False, icon=None, color: Optional[QColor] = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if icon is not None:
        item.setIcon(QIcon(icon))
    if color is not None:
        item.setForeground(color)
    return item


class Section(QWidget):
    """One part of the main window (Market, Rivens or Relics): a stack of pages, one shown at a time.

    Subclasses fill `PAGES` (page key -> sidebar label, in order), call `add_page` for each, and
    override `page_shown` (refresh a page when it appears) and `refresh_account` (after sign in/out).
    Panels call `run_async` for network work, `show_toast` for a short message, `show_sign_in`, and
    `account_changed` after signing in or out, which also tells the main window (`account_switched`).
    """

    PAGES: dict[str, str] = {}
    _async_done = pyqtSignal(object, object)  # (callback, result), run on the UI thread
    account_switched = pyqtSignal()
    page_changed = pyqtSignal(str)  # a page key, when the section switches page itself

    def __init__(self) -> None:
        super().__init__()
        self.pages = QStackedWidget()
        self._page_by_key: dict[str, QWidget] = {}
        self.toast = QLabel(objectName="toast")
        self._toast_timer = QTimer(self, singleShot=True, interval=4000)
        self._toast_timer.timeout.connect(lambda: self.toast.setText(""))
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._async_done.connect(lambda callback, result: callback(result))

    def add_page(self, key: str, widget: QWidget) -> None:
        if widget.layout() is not None:  # pages line up with the section's own controls
            widget.layout().setContentsMargins(0, 14, 0, 0)
        self._page_by_key[key] = widget
        self.pages.addWidget(widget)

    def page(self, key: str) -> QWidget:
        return self._page_by_key[key]

    def show_page(self, key: str) -> None:
        self.pages.setCurrentWidget(self._page_by_key[key])
        self.page_shown(key)
        self.page_changed.emit(key)

    def current_page(self) -> str:
        current = self.pages.currentWidget()
        return next((k for k, w in self._page_by_key.items() if w is current), "")

    def page_shown(self, key: str) -> None:
        """A page came into view (override to refresh it)."""

    def refresh_account(self) -> None:
        """Signed in or out (override to update the pages)."""

    def account_changed(self) -> None:
        self.refresh_account()
        self.account_switched.emit()

    def show_sign_in(self) -> None:
        from .orders import SignInDialog

        SignInDialog(self).exec()

    def show_toast(self, text: str) -> None:
        self.toast.setText(text)
        self._toast_timer.start()

    def run_async(self, fn, on_ok, on_err=None) -> None:
        """Run `fn` off the UI thread; call on_ok(result) or on_err(exception) back on the UI thread."""
        def work():
            try:
                self._async_done.emit(on_ok, fn())
            except Exception as exc:  # reported to the page that asked, never crashes the UI
                log.warning("%s request failed: %s", type(self).__name__, exc)
                if on_err is not None:
                    self._async_done.emit(on_err, exc)

        self._executor.submit(work)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
