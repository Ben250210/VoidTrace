"""The theme: one accent colour on a navy base, changeable from the tray (Accent colour).

Stylesheets are written with the default accent (#4fa3ff, the logo's void blue) and its hover shade;
`apply()` swaps in the chosen accent. Windows register with `follow()` so a new accent shows at once,
and painted parts (the riven popup's border and price, the best reward card) read `accent()` when
they draw.
"""

from __future__ import annotations

import weakref
from typing import Callable

from PyQt6.QtGui import QColor

from . import settings

DEFAULT_ACCENT = "#4fa3ff"
DEFAULT_HOVER = "#74b8ff"
DEFAULT_ON_ACCENT = "#06101f"  # text on accent-coloured buttons
PRESETS = {
    "Void blue": DEFAULT_ACCENT,
    "Cyan": "#38d6e0",
    "Purple": "#a98bff",
    "Pink": "#ff7ac8",
    "Gold": "#e0b64f",
    "Green": "#5ccf8f",
    "Red": "#ff6b6b",
}

_listeners: list[Callable[[], None]] = []


def accent() -> QColor:
    color = QColor(settings.get("accent", DEFAULT_ACCENT))
    return color if color.isValid() else QColor(DEFAULT_ACCENT)


def accent_hex() -> str:
    return accent().name()


def hover_hex() -> str:
    return accent().lighter(125).name()


def on_accent_hex() -> str:
    """Dark text on a light accent, white on a dark one."""
    return DEFAULT_ON_ACCENT if accent().lightness() > 110 else "#ffffff"


def apply(stylesheet: str) -> str:
    """A stylesheet written with the default accent, in the chosen one."""
    return (stylesheet.replace(DEFAULT_HOVER, hover_hex())
            .replace(DEFAULT_ACCENT, accent_hex())
            .replace(DEFAULT_ON_ACCENT, on_accent_hex()))


def set_accent(color: str) -> None:
    if not QColor(color).isValid():
        raise ValueError(f"not a colour: {color}")
    settings.put("accent", QColor(color).name())
    for listener in list(_listeners):
        listener()


def follow(widget, stylesheet: str) -> None:
    """Style `widget` with the accent now, and again whenever it changes."""
    widget.setStyleSheet(apply(stylesheet))
    ref = weakref.ref(widget)

    def restyle() -> None:
        w = ref()
        if w is None:
            _listeners.remove(restyle)
            return
        try:
            w.setStyleSheet(apply(stylesheet))
            w.update()
        except RuntimeError:  # the Qt object is gone
            _listeners.remove(restyle)

    _listeners.append(restyle)


def on_change(callback: Callable[[], None]) -> None:
    _listeners.append(callback)
