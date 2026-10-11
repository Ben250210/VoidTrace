"""Notice when a riven card is on screen (Item Details or Inventory > Mods > Cycle).

The game logs the Cycle screen opening ("OmegaRerollSelection.lua: Diorama setup") but not its
closing, and logs nothing for Item Details, so the screen itself is watched instead. Every riven card
carries the same emblem at its top centre; while Warframe is the active window, the watcher grabs the
screen about every 0.7 s and matches that emblem at the two places it sits (a few milliseconds).

  * a card appears, or its text changes (another riven, or after a cycle): once two grabs in a row
    agree, `card_shown(frame, layout)` is emitted with the settled frame
  * the card is gone for two grabs in a row: `card_hidden()`

Matched on the user's 11 cards at 0.80-1.00; 13 other game screenshots scored at most 0.44.
Read-only, like the rest of VoidTrace: it only looks at the screen.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .cv_cleaner import crop
from .riven_ocr import CYCLE, ITEM_DETAILS, Layout, ref_box

log = logging.getLogger(__name__)

DATA = Path(__file__).parent / "data"
# Where the emblem sits on each screen (reference units around the screen centre), and its picture.
EMBLEMS = ((ITEM_DETAILS, (-22, -162, 44, 32), DATA / "emblem_item_details.png"),
           (CYCLE, (-27, -70, 54, 40), DATA / "emblem_cycle.png"))
EMBLEM_SIZE = (64, 48)
MATCH_MIN = 0.7          # positives 0.80-1.00, other screens <= 0.44
INTERVAL_MS = 700
CHANGE_MIN = 10.0        # mean grey-level difference of the text area that counts as a different card


def _emblem(frame: np.ndarray, box) -> np.ndarray:
    gray = cv2.cvtColor(crop(frame, ref_box(frame, box)), cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, EMBLEM_SIZE, interpolation=cv2.INTER_AREA)


_templates: dict[str, np.ndarray] = {}


def _template(path: Path) -> Optional[np.ndarray]:
    if str(path) not in _templates:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            log.warning("riven emblem template missing: %s", path)
            return None
        _templates[str(path)] = img[6:42, 8:56]  # the emblem itself, with room to shift a few pixels
    return _templates[str(path)]


def detect_card(frame: np.ndarray) -> Optional[Layout]:
    """Which screen shows a riven card right now, or None."""
    for layout, box, path in EMBLEMS:
        template = _template(path)
        if template is None:
            continue
        found = _emblem(frame, box)
        if found.size and float(cv2.matchTemplate(found, template, cv2.TM_CCOEFF_NORMED).max()) >= MATCH_MIN:
            return layout
    return None


def text_signature(frame: np.ndarray, layout: Layout) -> np.ndarray:
    """A small picture of the card's text, to notice when it shows another riven."""
    gray = cv2.cvtColor(crop(frame, ref_box(frame, layout.text)), cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA).astype(np.float32)


class RivenScreenWatcher(QObject):
    card_shown = pyqtSignal(object, object)   # (frame, Layout)
    card_hidden = pyqtSignal()

    def __init__(self, grab: Callable[[], np.ndarray], foreground: Optional[Callable[[], bool]] = None,
                 interval_ms: int = INTERVAL_MS) -> None:
        super().__init__()
        self.grab = grab
        self.foreground = foreground
        self.timer = QTimer(self, interval=interval_ms)
        self.timer.timeout.connect(self.tick)
        self._seen: Optional[np.ndarray] = None      # text of the card last announced
        self._pending: Optional[np.ndarray] = None   # text of a new card waiting to settle
        self._misses = 0
        self.showing = False

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self._hide()

    def tick(self) -> None:
        try:
            if self.foreground is not None and not self.foreground():
                self._miss()
                return
            frame = self.grab()
            layout = detect_card(frame)
        except Exception as exc:  # a failed grab must not stop the watcher
            log.debug("riven watch: %s", exc)
            return
        if layout is None:
            self._miss()
            return
        self._misses = 0
        text = text_signature(frame, layout)
        if self._seen is not None and _same(text, self._seen):
            self._pending = None
            return  # the card already announced is still there
        if self._pending is not None and _same(text, self._pending):
            # Two grabs agree: the card has finished animating in (or the new roll has settled).
            self._seen, self._pending = text, None
            self.showing = True
            self.card_shown.emit(frame, layout)
        else:
            self._pending = text

    def _miss(self) -> None:
        self._pending = None
        self._misses += 1
        if self._misses >= 2:
            self._hide()

    def _hide(self) -> None:
        self._seen = None
        if self.showing:
            self.showing = False
            self.card_hidden.emit()


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and float(np.abs(a - b).mean()) < CHANGE_MIN
