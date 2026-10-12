"""Shared look and building blocks for every VoidTrace page: the stylesheets, table and cell helpers,
the number box with -/+ buttons, the mouse-wheel guard, and the colours used across pages."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

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
    header = t.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, len(columns)):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    return t


def make_cell(text: str, right: bool = False, icon=None, color: Optional[QColor] = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if icon is not None:
        item.setIcon(QIcon(icon))
    if color is not None:
        item.setForeground(color)
    return item
