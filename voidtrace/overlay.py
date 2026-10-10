"""Phase 5: transparent, always-on-top overlay for Borderless Fullscreen.

Nothing is injected into the game: this is an ordinary top-level window drawn above it. A window
mask limits mouse input to the price cards themselves, so every click elsewhere (including on the
game's own reward cards) goes straight to Warframe. Clicking a card asks for that item's sellers.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QWidget

from .cv_cleaner import reward_band, reward_slots
from .icons import ducat_icon, platinum_icon
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
    card_clicked = pyqtSignal(int)  # slot index of a tradable card the player clicked

    def __init__(self, screen_geometry: QRect, device_pixel_ratio: float = 1.0, ui_scale: float = 1.0,
                 hide_after_ms: int = 20000) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # no taskbar entry
            | Qt.WindowType.WindowDoesNotAcceptFocus   # clicks arrive, focus stays with the game
        )
        self.setMouseTracking(True)
        self.hovered: Optional[int] = None
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
        self.setMask(self.input_region())
        self.show()
        self.raise_()
        self.update()
        self._hide_timer.start()

    def clear(self) -> None:
        self.cards, self.card_rects, self.hovered = [], [], None
        self.update()
        self.hide()

    def input_region(self) -> QRegion:
        """Only the card rectangles take mouse input; the rest of the screen stays the game's."""
        region = QRegion()
        for rect in self.card_rects:
            region = region.united(QRegion(rect.adjusted(-2, -2, 2, 2)))
        return region if not region.isEmpty() else QRegion(0, 0, 1, 1)

    def clickable(self, index: int) -> bool:
        card = self.cards[index]
        return card.resolution.item is not None and card.resolution.item.slug is not None

    def card_at(self, x: float, y: float) -> Optional[int]:
        for i, rect in enumerate(self.card_rects):
            if rect.contains(round(x), round(y)):
                return i
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        index = self.card_at(event.position().x(), event.position().y())
        index = index if index is not None and self.clickable(index) else None
        if index != self.hovered:
            self.hovered = index
            self.setCursor(Qt.CursorShape.PointingHandCursor if index is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.hovered is not None:
            self.hovered = None
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self.card_at(event.position().x(), event.position().y())
        if index is not None and self.clickable(index) and event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(index)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, (card, rect) in enumerate(zip(self.cards, self.card_rects)):
            self._paint_card(painter, card, rect, hovered=(i == self.hovered))
        painter.end()

    def _paint_card(self, p: QPainter, card: RewardCard, rect: QRect, hovered: bool = False) -> None:
        border = BEST if card.best else (TEXT if hovered else BORDER)
        p.setPen(QPen(border, 2 if (card.best or hovered) else 1))
        p.setBrush(BG)
        p.drawRoundedRect(rect, 6, 6)
        inner = rect.adjusted(8, 6, -8, -6)
        line_h = max(12, inner.height() // 4)
        ratio = float(self.dpr)

        def font(size: float, bold: bool = False) -> QFont:
            f = QFont("Segoe UI")
            f.setPixelSize(max(9, round(size)))
            f.setBold(bold)
            return f

        def row(n: int) -> QRect:
            return QRect(inner.x(), inner.y() + n * line_h, inner.width(), line_h)

        def text(n: int, value: str, color: QColor, size: float, bold: bool = False) -> None:
            p.setFont(font(size, bold))
            p.setPen(color)
            area = row(n)
            elided = p.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, area.width())
            p.drawText(area, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, elided)

        def amounts(n: int, parts: list, color: QColor, size: float, bold: bool = False) -> None:
            """A centred row mixing plain text and icon+number pairs: parts are str or (icon_fn, str)."""
            p.setFont(font(size, bold))
            metrics = p.fontMetrics()
            icon_px = max(10, round(size * 0.95))
            gap = max(2, icon_px // 4)
            widths = [metrics.horizontalAdvance(x) if isinstance(x, str)
                      else icon_px + gap + metrics.horizontalAdvance(x[1]) for x in parts]
            area = row(n)
            x = area.x() + (area.width() - sum(widths)) / 2
            p.setPen(color)
            for part, w in zip(parts, widths):
                if isinstance(part, str):
                    p.drawText(QRectF(x, area.y(), w + 1, area.height()), Qt.AlignmentFlag.AlignVCenter, part)
                else:
                    p.drawPixmap(QPointF(x, area.y() + (area.height() - icon_px) / 2), part[0](icon_px, ratio))
                    p.drawText(QRectF(x + icon_px + gap, area.y(), w, area.height()),
                               Qt.AlignmentFlag.AlignVCenter, part[1])
                x += w

        if card.name is None:
            # Never echo raw OCR: on a wrong screen it is junk (live test 2 printed terminal text).
            text(1, "Unreadable", WARN, line_h * 0.7, bold=True)
            return

        text(0, card.name, TEXT, line_h * 0.6, bold=True)
        price_color = BEST if card.best else TEXT
        if card.resolution.item.slug is None:
            text(1, "Untradable", price_color, line_h * 0.85, bold=True)
        elif card.pending:
            amounts(1, [(platinum_icon, "…")], price_color, line_h * 0.85, bold=True)
        elif card.price is None:
            text(1, "Price unavailable", price_color, line_h * 0.7, bold=True)
        else:
            amounts(1, [(platinum_icon, f"{card.price.price:g}")], price_color, line_h * 0.85, bold=True)
        amounts(2, [(ducat_icon, str(card.ducats))], MUTED, line_h * 0.6)

        if hovered:
            text(3, "Click for sellers", TEXT, line_h * 0.5, bold=True)
            return
        notes: list = []
        if card.price is not None:
            notes.append(price_basis_label(card.price))
            if card.price.sell_now is not None:
                notes += [" · buy now ", (platinum_icon, f"{card.price.sell_now:g}")]
            if card.price.stale:
                notes.append(" · stale")
        if card.resolution.ambiguous:
            notes.append((" · " if notes else "") + "uncertain match")
        warn = card.resolution.ambiguous or (card.price is not None and card.price.stale)
        amounts(3, notes, WARN if warn else MUTED, line_h * 0.5)


def cards_summary(cards: Optional[list[RewardCard]]) -> str:
    """One line per card, used for console logging alongside the overlay."""
    lines = []
    for card in cards or []:
        plat = f"{card.price.price:g}p ({price_basis_label(card.price)})" if card.price else "-"
        star = " *" if card.best else ""
        lines.append(f"  slot {card.slot}: {card.name or '<unreadable>'} {plat} {card.ducats}d{star}")
    return "\n".join(lines)
