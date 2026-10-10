"""Platinum and ducat icons, drawn with QPainter (own artwork, not Digital Extremes' images).

    platinum: a faceted blue-silver crystal
    ducat:    a gold coin with a raised rim and a diamond emblem
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
