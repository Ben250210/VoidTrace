"""Platinum and ducat icons, drawn with QPainter (own artwork, not Digital Extremes' images).

    platinum: a faceted blue-silver crystal
    ducat:    a gold coin with a raised rim and a diamond emblem
    chest:    a treasure chest, red for a vaulted relic and green for one that drops now
"""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient


def _canvas(size: int, ratio: float) -> tuple[QPixmap, QPainter]:
    px = QPixmap(round(size * ratio), round(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return px, p


@lru_cache(maxsize=32)
def platinum_icon(size: int, ratio: float = 1.0) -> QPixmap:
    px, p = _canvas(size, ratio)
    s = float(size)
    c = s / 2
    # Outline: a tall hexagonal gem.
    pts = [QPointF(c, s * 0.04), QPointF(s * 0.88, s * 0.30), QPointF(s * 0.88, s * 0.70),
           QPointF(c, s * 0.96), QPointF(s * 0.12, s * 0.70), QPointF(s * 0.12, s * 0.30)]
    gem = QPolygonF(pts)
    grad = QLinearGradient(QPointF(0, 0), QPointF(s, s))
    grad.setColorAt(0.0, QColor(214, 236, 255))
    grad.setColorAt(0.5, QColor(120, 176, 230))
    grad.setColorAt(1.0, QColor(56, 98, 168))
    p.setPen(QPen(QColor(30, 52, 96), max(1.0, s / 18)))
    p.setBrush(QBrush(grad))
    p.drawPolygon(gem)
    # Facets: a lighter top-left plane and lines to an inner point.
    inner = QPointF(c, s * 0.46)
    light = QPainterPath()
    light.moveTo(pts[0]); light.lineTo(inner); light.lineTo(pts[5]); light.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 110))
    p.drawPath(light)
    p.setPen(QPen(QColor(235, 245, 255, 170), max(0.8, s / 30)))
    for pt in (pts[0], pts[1], pts[3], pts[5]):
        p.drawLine(inner, pt)
    p.end()
    return px


VAULTED_RED = "#ff6b6b"
FARMABLE_GREEN = "#5ccf8f"


@lru_cache(maxsize=8)
def chest_icon(size: int, vaulted: bool, ratio: float = 1.0) -> QPixmap:
    """A small treasure chest: rounded lid, box, dark latch. Red when vaulted, green when farmable."""
    px, p = _canvas(size, ratio)
    s = float(size)
    fill = QColor(VAULTED_RED if vaulted else FARMABLE_GREEN)
    edge = QPen(fill.darker(220), max(1.0, s / 20))
    p.setPen(edge)
    p.setBrush(fill)
    lid = QPainterPath()
    lid.moveTo(s * 0.10, s * 0.44)
    lid.lineTo(s * 0.10, s * 0.30)
    lid.quadTo(s * 0.50, s * 0.04, s * 0.90, s * 0.30)
    lid.lineTo(s * 0.90, s * 0.44)
    lid.closeSubpath()
    p.drawPath(lid)
    p.drawRect(QRectF(s * 0.10, s * 0.44, s * 0.80, s * 0.46))  # the box
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill.darker(250))
    p.drawRect(QRectF(s * 0.43, s * 0.36, s * 0.14, s * 0.16))  # the latch, across lid and box
    p.end()
    return px


@lru_cache(maxsize=32)
def ducat_icon(size: int, ratio: float = 1.0) -> QPixmap:
    px, p = _canvas(size, ratio)
    s = float(size)
    outer = QRectF(s * 0.05, s * 0.05, s * 0.90, s * 0.90)
    grad = QRadialGradient(QPointF(s * 0.38, s * 0.32), s * 0.7)
    grad.setColorAt(0.0, QColor(255, 236, 160))
    grad.setColorAt(0.55, QColor(222, 170, 60))
    grad.setColorAt(1.0, QColor(150, 98, 24))
    p.setPen(QPen(QColor(110, 70, 16), max(1.0, s / 18)))
    p.setBrush(QBrush(grad))
    p.drawEllipse(outer)
    # Raised rim.
    p.setPen(QPen(QColor(255, 226, 140, 200), max(0.8, s / 22)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60))
    # Diamond emblem.
    c = s / 2
    d = s * 0.17
    emblem = QPolygonF([QPointF(c, c - d), QPointF(c + d, c), QPointF(c, c + d), QPointF(c - d, c)])
    p.setPen(QPen(QColor(120, 76, 18), max(0.8, s / 26)))
    p.setBrush(QColor(255, 240, 190))
    p.drawPolygon(emblem)
    p.end()
    return px


@lru_cache(maxsize=16)
def voidtrace_logo(size: int, ratio: float = 1.0) -> QPixmap:
    """VoidTrace logo: a glowing void orb wrapped in swept wisps, after the game's Void Traces resource
    (own drawing, not the game's artwork), on a dark round badge so it reads on light and dark
    backgrounds. Small sizes drop the fine detail."""
    px, p = _canvas(size, ratio)
    s = float(size)
    c = QPointF(s / 2, s / 2)
    detailed = size >= 32

    # Badge: deep void blue-violet, lighter toward the top left, with a faint cyan rim.
    badge = QRadialGradient(QPointF(s * 0.38, s * 0.32), s * 0.7)
    badge.setColorAt(0.0, QColor(36, 42, 92))
    badge.setColorAt(0.6, QColor(16, 18, 46))
    badge.setColorAt(1.0, QColor(8, 9, 24))
    p.setBrush(QBrush(badge))
    p.setPen(QPen(QColor(120, 200, 255, 150), max(1.0, s / 42)))
    p.drawEllipse(c, s * 0.47, s * 0.47)

    # Glow around the orb.
    glow = QRadialGradient(c, s * 0.36)
    glow.setColorAt(0.0, QColor(150, 220, 255, 170))
    glow.setColorAt(0.5, QColor(80, 160, 255, 60))
    glow.setColorAt(1.0, QColor(60, 120, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(c, s * 0.36, s * 0.36)

    # Wisps sweeping around the orb, rotationally symmetric.
    def wisp(angle: float, alpha: int, reach: float) -> None:
        p.save()
        p.translate(c)
        p.rotate(angle)
        path = QPainterPath()
        path.moveTo(-reach, s * 0.05)
        path.cubicTo(-reach * 0.92, -reach * 0.78, reach * 0.30, -reach * 1.12, reach * 0.95, -reach * 0.50)
        path.cubicTo(reach * 0.30, -reach * 0.80, -reach * 0.50, -reach * 0.62, -reach, s * 0.05)
        grad = QLinearGradient(QPointF(-reach, 0), QPointF(reach * 0.95, -reach * 0.5))
        grad.setColorAt(0.0, QColor(110, 190, 255, 0))
        grad.setColorAt(0.45, QColor(150, 215, 255, alpha))
        grad.setColorAt(1.0, QColor(240, 252, 255, min(255, alpha + 45)))
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        p.restore()

    reach = s * 0.38
    wisp(-20, 235, reach)
    wisp(160, 235, reach)
    if detailed:
        wisp(70, 110, reach * 0.86)
        wisp(250, 110, reach * 0.86)

    # The orb: white-hot centre fading to blue at the rim.
    orb_r = s * (0.15 if detailed else 0.17)
    orb = QRadialGradient(QPointF(s * 0.47, s * 0.46), orb_r * 1.2)
    orb.setColorAt(0.0, QColor(255, 255, 255))
    orb.setColorAt(0.4, QColor(220, 243, 255))
    orb.setColorAt(0.8, QColor(120, 190, 255))
    orb.setColorAt(1.0, QColor(55, 110, 225))
    p.setBrush(QBrush(orb))
    p.drawEllipse(c, orb_r, orb_r)

    # Sparks drifting off the wisps.
    if detailed:
        for x, y, r in ((0.79, 0.27, 0.020), (0.21, 0.74, 0.017), (0.70, 0.80, 0.013), (0.28, 0.22, 0.012)):
            p.setBrush(QColor(225, 247, 255, 230))
            p.drawEllipse(QPointF(s * x, s * y), s * r, s * r)
    p.end()
    return px


@lru_cache(maxsize=16)
def eye_icon(size: int, visible: bool, ratio: float = 1.0) -> QPixmap:
    """Order visibility: a green open eye when visible, a red eye with a slash when hidden."""
    px, p = _canvas(size, ratio)
    s = float(size)
    color = QColor(124, 201, 156) if visible else QColor(240, 110, 90)
    pen = QPen(color, max(1.4, s / 10))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    almond = QPainterPath()
    almond.moveTo(s * 0.06, s * 0.5)
    almond.quadTo(s * 0.5, s * 0.08, s * 0.94, s * 0.5)
    almond.quadTo(s * 0.5, s * 0.92, s * 0.06, s * 0.5)
    p.drawPath(almond)
    p.setBrush(color)
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.15, s * 0.15)
    if not visible:
        p.setPen(QPen(QColor(11, 18, 32), max(3.0, s / 5)))  # gap so the slash reads clearly
        p.drawLine(QPointF(s * 0.16, s * 0.84), QPointF(s * 0.84, s * 0.16))
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.16, s * 0.84), QPointF(s * 0.84, s * 0.16))
    p.end()
    return px


def draw_amount(painter: QPainter, rect: QRect, icon: QPixmap, text: str, font: QFont, color: QColor,
                align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter) -> None:
    """Draw `icon` + `text` as one unit inside `rect`, vertically centred."""
    painter.setFont(font)
    metrics = painter.fontMetrics()
    icon_w = icon.width() / icon.devicePixelRatio()
    icon_h = icon.height() / icon.devicePixelRatio()
    gap = max(3, round(icon_w * 0.25))
    total = icon_w + gap + metrics.horizontalAdvance(text)
    if align == Qt.AlignmentFlag.AlignLeft:
        x = rect.x()
    elif align == Qt.AlignmentFlag.AlignRight:
        x = rect.right() - total
    else:
        x = rect.x() + (rect.width() - total) / 2
    painter.drawPixmap(QPointF(x, rect.y() + (rect.height() - icon_h) / 2), icon)
    painter.setPen(color)
    painter.drawText(QRectF(x + icon_w + gap, rect.y(), rect.width(), rect.height()),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
