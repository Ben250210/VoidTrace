"""The market window's Alerts tab: your price alerts, with Delete. Add one from an item's
"Alert me" button (see alerts.py for how they are checked)."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..alerts import Alert, load_alerts, remove_alert
from ..icons import platinum_icon
from .widgets import BUTTON_STYLE, make_cell, make_table


class AlertsPanel(QWidget):
    COLUMNS = ["Item", "Rank", "At or under", ""]
    ITEM, RANK, PRICE, DELETE = range(4)

    def __init__(self, on_changed: Callable[[], None] = lambda: None) -> None:
        super().__init__()
        self.on_changed = on_changed
        self.alerts: list[Alert] = []
        theme.follow(self, BUTTON_STYLE)
        self.table = make_table(self.COLUMNS)
        self.table.horizontalHeaderItem(self.PRICE).setIcon(QIcon(platinum_icon(14)))
        self.status = QLabel(objectName="muted")
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("Checked every minute while VoidTrace runs. A tray message appears when someone "
                                "lists at or under your price; click it to copy the whisper.",
                                objectName="muted", wordWrap=True))

    def refresh(self) -> None:
        self.alerts = load_alerts()
        self.table.setRowCount(len(self.alerts))
        for row, alert in enumerate(self.alerts):
            self.table.setItem(row, self.ITEM, make_cell(alert.name))
            self.table.setItem(row, self.RANK, make_cell("" if alert.rank is None else str(alert.rank), right=True))
            self.table.setItem(row, self.PRICE, make_cell(str(alert.max_price), right=True, icon=platinum_icon(14)))
            delete = QPushButton("Delete", objectName="danger")
            delete.setCursor(Qt.CursorShape.PointingHandCursor)
            delete.clicked.connect(lambda _=False, a=alert: self.delete(a))
            self.table.setCellWidget(row, self.DELETE, delete)
        self.status.setText(f"{len(self.alerts)} price alert(s)" if self.alerts else
                            "No alerts yet. Open an item and click “Alert me”.")

    def delete(self, alert: Alert) -> None:
        remove_alert(alert)
        self.refresh()
        self.on_changed()
