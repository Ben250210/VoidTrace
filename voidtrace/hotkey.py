"""Global hotkeys from inside the game: Ctrl+Shift+M opens Market > Buy, Ctrl+Shift+R Rivens > Price check.

Uses the standard Win32 RegisterHotKey: Windows tells VoidTrace when the combination is pressed.
Nothing is sent to or read from Warframe.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5654  # "VT"
RIVEN_HOTKEY_ID = 0x5652  # "VR"


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback: Callable[[], None], hotkey_id: int = HOTKEY_ID) -> None:
        super().__init__()
        self.callback = callback
        self.hotkey_id = hotkey_id

    def handle(self, message: int, wparam: int) -> bool:
        if message == WM_HOTKEY and wparam == self.hotkey_id:
            self.callback()
            return True
        return False

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt naming)
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        return self.handle(msg.message, msg.wParam), 0


def register(app, callback: Callable[[], None], key: str = "M", hotkey_id: int = HOTKEY_ID) -> HotkeyFilter | None:
    """Register Ctrl+Shift+<key>. Returns the filter (keep a reference) or None if unavailable."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord(key.upper())):
        log.warning("Ctrl+Shift+%s is already taken by another program; use the tray menu instead", key.upper())
        return None
    hotkey = HotkeyFilter(callback, hotkey_id)
    app.installNativeEventFilter(hotkey)
    log.info("hotkey: Ctrl+Shift+%s", key.upper())
    return hotkey


def unregister(hotkey_id: int = HOTKEY_ID) -> None:
    if sys.platform == "win32":
        ctypes.WinDLL("user32").UnregisterHotKey(None, hotkey_id)
