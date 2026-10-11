"""The riven popup: a compact panel at the right edge of the screen, shown while a riven card is open
in Item Details or on the Cycle screen.

It shows the riven, each stat with how well it rolled, and the price estimate. Clicking it opens the
full riven window on that riven. It never takes keyboard focus from the game.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtCore import QObject, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .icons import draw_amount, platinum_icon, voidtrace_logo
from .rivens import PriceCheck, RivenCatalog, RivenDraft, attr_rolls, grade, price_check, riven_grade

if TYPE_CHECKING:
    from .rivens import RivenService

log = logging.getLogger(__name__)

WIDTH = 330
PANEL = QColor(11, 18, 32, 238)
BORDER = QColor(79, 163, 255)
TEXT = QColor(230, 238, 251)
MUTED = QColor(143, 163, 194)
BUFF = QColor(124, 201, 156)
CURSE = QColor(240, 110, 90)
AMBER = QColor(230, 180, 80)


def grade_color(value: int) -> QColor:
    t = value / 100
    return QColor(int(240 - 116 * t), int(110 + 91 * t), int(90 + 66 * t))


class GradeBar(QWidget):
    """A thin bar filled to the stat's grade, with the number beside it."""

    def __init__(self, value: Optional[int]) -> None:
        super().__init__()
        self.value = value
        self.setFixedSize(92, 16)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.value is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 5, 54, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(34, 49, 79))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(grade_color(self.value))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(4.0, bar.width() * self.value / 100), bar.height()), 3, 3)
        p.setPen(grade_color(self.value))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(QRect(60, 0, 32, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self.value}%")
        p.end()


class PriceLine(QWidget):
    """The estimate with the platinum icon, drawn like the reward-screen cards."""

    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.setFixedHeight(30)

    def set_text(self, text: str) -> None:
        self.text = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self.text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Segoe UI", 15, QFont.Weight.DemiBold)
        draw_amount(p, self.rect(), platinum_icon(20, self.devicePixelRatioF()), self.text, font,
                    QColor(140, 200, 255), Qt.AlignmentFlag.AlignLeft)
        p.end()


class RivenPopup(QWidget):
    clicked = pyqtSignal()

    def __init__(self, screen_geometry: QRect) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(WIDTH)
        self.screen_geometry = screen_geometry
        self.draft: Optional[RivenDraft] = None
        self.check: Optional[PriceCheck] = None
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 14)
        self.body.setSpacing(6)
        self.price = PriceLine()
        self.price_detail = self._label("", MUTED, 9)
        self.price_detail.setWordWrap(True)

    # --- content ------------------------------------------------------------------------

    def _label(self, text: str, color: QColor, size: int = 10, bold: bool = False) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {color.name()}; background: transparent;")
        label.setFont(QFont("Segoe UI", size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
        return label

    def _clear(self) -> None:
        # Detach at once: deleteLater alone left the previous riven's rows drawn under the new ones.
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget() is not None and item.widget() not in (self.price, self.price_detail):
                _drop(item.widget())
            elif item.layout() is not None:
                _delete_layout(item.layout())

    def show_reading(self) -> None:
        self.draft, self.check = None, None
        self._clear()
        self.body.addLayout(self._header("Reading riven…"))
        self._place()

    def show_read(self, catalog: RivenCatalog, draft: RivenDraft, warnings: list[str]) -> None:
        """Fill in the riven; the price follows with show_price()."""
        self.draft, self.check = draft, None
        self._clear()
        weapon = catalog.by_weapon.get(draft.weapon)
        title = f"{weapon.name if weapon else draft.weapon} {draft.name[:1].upper()}{draft.name[1:]}".strip()
        self.body.addLayout(self._header(title or "Riven"))
        legend = self._label("Bars: how well each stat rolled, within what this riven can roll", MUTED, 8)
        legend.setWordWrap(True)
        self.body.addWidget(legend)
        rolls = attr_rolls(catalog, weapon, draft.attrs, draft.rank) if weapon else [None] * len(draft.attrs)
        for attr, roll in zip(draft.attrs, rolls):
            g = grade(roll) if roll is not None and 0.85 <= roll <= 1.15 else None
            if g is not None and not attr.positive:
                g = 100 - g  # a smaller curse is the better roll
            row = QHBoxLayout()
            row.addWidget(self._label(catalog.format_attr(attr), BUFF if attr.positive else CURSE, 10), 1)
            row.addWidget(GradeBar(g))
            self.body.addLayout(row)
        overall = riven_grade(catalog, weapon, draft.attrs, draft.rank) if weapon else None
        if overall is not None:
            self.body.addWidget(self._label(f"Overall roll {overall}%  (0% = worst possible, 100% = best)", TEXT, 9))
        meta = f"MR {draft.mr} · {draft.rolls} rolls · rank {draft.rank} · {draft.polarity.title()}"
        self.body.addWidget(self._label(meta, MUTED, 9))
        self.price.set_text("")
        self.price_detail.setText("Checking listings…")
        self.body.addWidget(self.price)
        self.body.addWidget(self.price_detail)
        for warning in warnings[:2]:
            w = self._label(warning, AMBER, 9)
            w.setWordWrap(True)
            self.body.addWidget(w)
        self.body.addWidget(self._label("Click for details", MUTED, 8))
        self._place()

    def show_price(self, check: Optional[PriceCheck], error: str = "") -> None:
        self.check = check
        if error:
            self.price.set_text("")
            self.price_detail.setText(f"No price: {error}")
        elif check is None or check.estimate is None:
            self.price.set_text("")
            self.price_detail.setText("Nobody is selling a riven for this weapon right now.")
        else:
            self.price.set_text(f"~{check.estimate:g}")
            similar = ", similar rolls" if check.similar_rolls else ""
            self.price_detail.setText(f"{check.count} listings with {check.tier}{similar} "
                                      f"({check.low}-{check.high}p)")
        self._place()

    def show_failed(self, message: str) -> None:
        self.draft, self.check = None, None
        self._clear()
        self.body.addLayout(self._header("Riven not read"))
        w = self._label(message, AMBER, 9)
        w.setWordWrap(True)
        self.body.addWidget(w)
        self.body.addWidget(self._label("Click to open the riven window", MUTED, 8))
        self._place()

    def _header(self, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(voidtrace_logo(20, self.devicePixelRatioF()))
        logo.setStyleSheet("background: transparent;")
        row.addWidget(logo)
        name = self._label(title, TEXT, 12, bold=True)
        name.setWordWrap(True)
        row.addWidget(name, 1)
        return row

    # --- window -------------------------------------------------------------------------

    def _place(self) -> None:
        """Right edge, a fifth of the way down: clear of the card in the middle and AlecaFrame on the left."""
        self.adjustSize()
        g = self.screen_geometry
        self.move(g.right() - self.width() - 28, g.top() + int(g.height() * 0.2))
        if not self.isVisible():
            self.show()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(BORDER, 1.5))
        p.setBrush(PANEL)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


def _drop(widget: QWidget) -> None:
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            _drop(item.widget())
        elif item.layout() is not None:
            _delete_layout(item.layout())


class RivenAssistant(QObject):
    """Ties the screen watcher to the popup: read the card off the screen, price it, show both."""

    _done = pyqtSignal(object, object)  # (callback, result) back on the UI thread

    def __init__(self, data: Callable[[], tuple[RivenCatalog, "RivenService"]], popup: RivenPopup,
                 my_name: Callable[[], Optional[str]], open_window: Callable[[Optional[RivenDraft], Optional[PriceCheck]], None],
                 read: Optional[Callable] = None) -> None:
        super().__init__()
        from .riven_ocr import read_riven

        self.data = data
        self.popup = popup
        self.my_name = my_name
        self.open_window = open_window
        self.read = read or read_riven
        self._generation = 0
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._done.connect(lambda callback, result: callback(result))
        popup.clicked.connect(self._clicked)

    def card_shown(self, frame, _layout=None) -> None:
        self._generation += 1
        generation = self._generation
        if self.popup.draft is None or not self.popup.isVisible():
            self.popup.show_reading()  # a re-read of a showing riven happens quietly behind it

        def work():
            catalog, _ = self.data()
            return catalog, self.read(frame, catalog)

        self._run(work, lambda result: self._read_done(generation, *result), generation)

    def card_hidden(self) -> None:
        self._generation += 1  # results still on their way are dropped
        self.popup.hide()

    def card_back(self) -> None:
        """The same riven is showing again after a short gap: bring the popup back as it was."""
        if self.popup.draft is not None:
            self.popup.show()

    def _read_done(self, generation: int, catalog: RivenCatalog, read) -> None:
        if generation != self._generation:
            return
        current = self.popup.draft
        if not read.ok:
            if current is not None:  # keep the good read rather than flash "not read"
                self.popup.show()
                return
            self.popup.show_failed(" ".join(read.warnings) or "Could not read this riven.")
            return
        draft = read.draft
        if current is not None and (draft.weapon, draft.name, draft.attrs) == (current.weapon, current.name, current.attrs):
            self.popup.show()  # same riven read again: nothing on screen changes
            return
        self.popup.show_read(catalog, draft, read.warnings)
        me = self.my_name()

        def work():
            _, service = self.data()
            weapon = catalog.by_weapon.get(draft.weapon)
            mine = riven_grade(catalog, weapon, draft.attrs, draft.rank) if weapon else None
            return price_check(service.search(draft.weapon), draft.positives, draft.negative, exclude_seller=me,
                               catalog=catalog, my_grade=mine)

        self._run(work, lambda check: generation == self._generation and self.popup.show_price(check), generation,
                  on_error=lambda exc: self.popup.show_price(None, str(exc)))

    def _run(self, fn, on_ok, generation: int, on_error=None) -> None:
        def task():
            try:
                self._done.emit(on_ok, fn())
            except Exception as exc:
                log.warning("riven popup: %s", exc)
                if generation == self._generation:
                    self._done.emit(on_error or (lambda e: self.popup.show_failed(str(e))), exc)

        self._executor.submit(task)

    def _clicked(self) -> None:
        draft, check = self.popup.draft, self.popup.check
        self.popup.hide()
        self.open_window(draft, check)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
