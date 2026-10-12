"""The Discord panel (sidebar > Discord): paste a webhook link, pick what to send, send a test."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import settings, theme
from ..discord import KINDS, DiscordNotifier, is_webhook, kind_on
from .widgets import BUTTON_STYLE, STYLE

HOW_TO = ("In Discord: Server Settings › Integrations › Webhooks › New Webhook › pick a channel "
          "› Copy Webhook URL. Paste it here. The link is kept in Windows Credential Manager.")


class DiscordDialog(QDialog):
    def __init__(self, parent, notifier: DiscordNotifier) -> None:
        super().__init__(parent)
        self.notifier = notifier
        self.setWindowTitle("VoidTrace · Discord")
        self.setStyleSheet(theme.apply(STYLE + BUTTON_STYLE))
        self.link = QLineEdit(placeholderText="https://discord.com/api/webhooks/...")
        self.link.setEchoMode(QLineEdit.EchoMode.Password)  # it works like a password
        self.link.setText(notifier.url() or "")
        self.boxes = {kind: QCheckBox(label) for kind, label in KINDS.items()}
        for kind, box in self.boxes.items():
            box.setChecked(kind_on(kind))
        self.status = QLabel(notifier.last_error or ("Connected." if notifier.url() else "Not connected."),
                             objectName="muted", wordWrap=True)
        buttons = QHBoxLayout()
        for text, slot, name in (("Save", self.save, "primary"), ("Send test", self.send_test, ""),
                                 ("Remove", self.remove, "danger")):
            button = QPushButton(text, objectName=name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(HOW_TO, objectName="muted", wordWrap=True))
        layout.addWidget(self.link)
        for box in self.boxes.values():
            layout.addWidget(box)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        self.resize(460, 0)

    def save(self) -> bool:
        url = self.link.text().strip()
        if not is_webhook(url):
            self.status.setText("That is not a webhook link. It starts with https://discord.com/api/webhooks/")
            return False
        self.notifier.set_url(url)
        for kind, box in self.boxes.items():
            settings.put(f"discord_{kind}", box.isChecked())
        self.status.setText("Saved.")
        return True

    def send_test(self) -> None:
        if self.save():
            self.notifier.send("test", "VoidTrace is connected", "Notices from VoidTrace will show up here.", force=True)
            self.status.setText("Test sent: check your Discord channel.")

    def remove(self) -> None:
        self.notifier.set_url(None)
        self.link.clear()
        self.status.setText("Removed. Nothing is sent to Discord.")
