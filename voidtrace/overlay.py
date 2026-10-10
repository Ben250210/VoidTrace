"""Phase 5: transparent, click-through, always-on-top overlay for Borderless Fullscreen.

Nothing is injected into the game: this is an ordinary top-level window drawn above it.
WindowTransparentForInput makes every click fall through to Warframe.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from .cv_cleaner import reward_band, reward_slots
from .pipeline import RewardCard
from .price_cleaner import price_basis_label

CARD_WIDTH_RATIO = 0.95   # card width as a share of one reward slot
CARD_HEIGHT = 92          # reference pixels at 1080p; scaled with the screen
# Gap between the reward-name band and the cards. Under each reward the game lists the
# squad members who picked it (up to 4 names, ~110 px at 1080p); the first live test
# showed cards placed right under the names covering that list, so they sit below it.
GAP_BELOW_NAMES = 130

BG = QColor(12, 14, 20, 215)
BORDER = QColor(70, 80, 100, 230)
BEST = QColor(232, 190, 90, 255)
TEXT = QColor(235, 238, 245)
MUTED = QColor(150, 158, 175)
WARN = QColor(240, 140, 90)


class OverlayWindow(QWidget):
    def __init__(self, screen_geometry: QRect, device_pixel_ratio: float = 1.0, ui_scale: float = 1.0,
                 hide_after_ms: int = 20000) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # no taskbar entry
            | Qt.WindowType.WindowTransparentForInput  # click-through
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen_geometry)
        self.dpr = device_pixel_ratio
        self.ui_scale = ui_scale
        self.cards: list[RewardCard] = []
        self.card_rects: list[QRect] = []
        self._hide_timer = QTimer(self, singleShot=True, interval=hide_after_ms)
        self._hide_timer.timeout.connect(self.clear)

    # Geometry works in physical pixels (what the screenshot sees), then converts to Qt's
    # logical pixels so it lines up under Windows display scaling.
    def layout_cards(self, count: int) -> list[QRect]:
        phys_w = round(self.width() * self.dpr)
        phys_h = round(self.height() * self.dpr)
        band = reward_band(phys_w, phys_h, self.ui_scale)
        scale = phys_h / 1080 * self.ui_scale
        rects = []
        for box in reward_slots(phys_w, phys_h, count, self.ui_scale):
            w = box.w * CARD_WIDTH_RATIO
            x = box.x + (box.w - w) / 2
            y = band.y + band.h + GAP_BELOW_NAMES * scale
            h = CARD_HEIGHT * scale
            rects.append(QRect(*(round(v / self.dpr) for v in (x, y, w, h))))
        return rects

    def show_cards(self, cards: list[RewardCard]) -> None:
        self.cards = cards
        self.card_rects = self.layout_cards(len(cards)) if cards else []
        self.show()
        self.raise_()
        self.update()
        self._hide_timer.start()

    def clear(self) -> None:
        self.cards, self.card_rects = [], []
        self.update()
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for card, rect in zip(self.cards, self.card_rects):
            self._paint_card(painter, card, rect)
        painter.end()

    def _paint_card(self, p: QPainter, card: RewardCard, rect: QRect) -> None:
        p.setPen(QPen(BEST if card.best else BORDER, 2 if card.best else 1))
        p.setBrush(BG)
        p.drawRoundedRect(rect, 6, 6)
        inner = rect.adjusted(8, 6, -8, -6)
        line_h = max(12, inner.height() // 4)

        def text(row: int, value: str, color: QColor, size: float, bold: bool = False) -> None:
            font = QFont("Segoe UI")
            font.setPixelSize(max(9, round(size)))
            font.setBold(bold)
            p.setFont(font)
            p.setPen(color)
            area = QRect(inner.x(), inner.y() + row * line_h, inner.width(), line_h)
            elided = p.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, area.width())
            p.drawText(area, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, elided)

        if card.name is None:
            # Never echo raw OCR: on a wrong screen it is junk (live test 2 printed terminal text).
            text(1, "Unreadable", WARN, line_h * 0.7, bold=True)
            return

        text(0, card.name, TEXT, line_h * 0.6, bold=True)
        if card.resolution.item.slug is None:
            plat = "Untradable"
        elif card.pending:
            plat = "price…"
        elif card.price is None:
            plat = "Price unavailable"
        else:
            plat = f"{card.price.price:g} plat"
        text(1, plat, BEST if card.best else TEXT, line_h * 0.85, bold=True)
        text(2, f"{card.ducats} ducats", MUTED, line_h * 0.55)

        notes = []
        if card.price is not None:
            notes.append(price_basis_label(card.price))
            if card.price.sell_now is not None:
                notes.append(f"buy now {card.price.sell_now:g}p")
            if card.price.stale:
                notes.append("stale")
        if card.resolution.ambiguous:
            notes.append("uncertain match")
        warn = card.resolution.ambiguous or (card.price is not None and card.price.stale)
        text(3, " · ".join(notes), WARN if warn else MUTED, line_h * 0.5)


def cards_summary(cards: Optional[list[RewardCard]]) -> str:
    """One line per card, used for console logging alongside the overlay."""
    lines = []
    for card in cards or []:
        plat = f"{card.price.price:g}p ({price_basis_label(card.price)})" if card.price else "-"
        star = " *" if card.best else ""
        lines.append(f"  slot {card.slot}: {card.name or '<unreadable>'} {plat} {card.ducats}d{star}")
    return "\n".join(lines)
