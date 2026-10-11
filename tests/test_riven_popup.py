"""The riven popup: noticing a riven card on screen, and the panel it opens at the right edge."""

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from test_riven_ocr import EXPECTED, screen
from voidtrace import rivens
from voidtrace.riven_ocr import CYCLE, ITEM_DETAILS, RivenRead
from voidtrace.riven_popup import RivenAssistant, RivenPopup
from voidtrace.riven_watch import RivenScreenWatcher, detect_card
from voidtrace.rivens import PriceCheck, RivenAttr, RivenDraft

FIXTURES = Path(__file__).parent / "fixtures" / "rivens"
CYCLE_CARDS = {"efv_8_mars_veximag", "euphona_prime_croni-vexitron", "akvasto_acri-decitin",
               "akmagnus_sci-critaata", "burston_sati-visicon_cycle_prime"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def catalog():
    return rivens.RivenCatalog(
        rivens.parse_weapons(json.loads((FIXTURES / "weapons.json").read_text())["data"]),
        rivens.parse_stats(json.loads((FIXTURES / "attributes.json").read_text())["data"]))


def _wait(qapp, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


# --- noticing the card --------------------------------------------------------------------

@pytest.mark.parametrize("key", list(EXPECTED))
def test_card_is_found_on_both_screens(key):
    assert detect_card(screen(key)) is (CYCLE if key in CYCLE_CARDS else ITEM_DETAILS)


def test_no_card_on_an_empty_screen():
    assert detect_card(np.zeros((1600, 2560, 3), dtype=np.uint8)) is None


def test_watcher_announces_a_settled_card_once_and_hides_it(qapp):
    blank = np.zeros((1600, 2560, 3), dtype=np.uint8)
    hikou, burston = screen("hikou_igni-insitron"), screen("burston_sati-visicon")
    frames = iter([blank, hikou, hikou, hikou, burston, burston, burston, blank, blank, blank])
    shown, hidden = [], []
    watcher = RivenScreenWatcher(lambda: next(frames))
    watcher.card_shown.connect(lambda frame, layout: shown.append(frame is hikou or frame is burston))
    watcher.card_hidden.connect(lambda: hidden.append(True))
    for _ in range(10):
        watcher.tick()
    assert len(shown) == 2       # the Hikou once (two agreeing grabs), the Burston once (three, as a change)
    assert hidden == [True]      # after three empty grabs


def _streaked(frame, seed):
    """The same card with a bright diagonal streak across its text, like the Cycle screen's lightning."""
    import cv2
    from voidtrace.riven_ocr import ref_box

    out = frame.copy()
    box = ref_box(out, ITEM_DETAILS.text)
    rng = np.random.default_rng(seed)
    x0, x1 = rng.integers(box.x, box.x + box.w, size=2)
    cv2.line(out, (int(x0), box.y), (int(x1), box.y + box.h), (255, 180, 230), 3)
    return out


def test_lightning_over_the_card_is_not_a_new_riven(qapp):
    hikou = screen("hikou_igni-insitron")
    frames = iter([hikou, hikou] + [_streaked(hikou, i) for i in range(12)])
    shown = []
    watcher = RivenScreenWatcher(lambda: next(frames))
    watcher.card_shown.connect(lambda frame, layout: shown.append(True))
    for _ in range(14):
        watcher.tick()
    assert shown == [True]  # read once; twelve flashes later, still once


def test_a_short_gap_brings_the_same_card_back_without_a_new_read(qapp):
    blank = np.zeros((1600, 2560, 3), dtype=np.uint8)
    hikou = screen("hikou_igni-insitron")
    frames = iter([hikou, hikou, blank, blank, blank, hikou])
    shown, hidden, back = [], [], []
    watcher = RivenScreenWatcher(lambda: next(frames))
    watcher.card_shown.connect(lambda frame, layout: shown.append(True))
    watcher.card_hidden.connect(lambda: hidden.append(True))
    watcher.card_back.connect(lambda: back.append(True))
    for _ in range(6):
        watcher.tick()
    assert (len(shown), len(hidden), len(back)) == (1, 1, 1)


def test_watcher_hides_when_warframe_is_not_in_front(qapp):
    hikou = screen("hikou_igni-insitron")
    in_front = iter([True, True, False, False, False])
    hidden = []
    watcher = RivenScreenWatcher(lambda: hikou, lambda: next(in_front))
    watcher.card_hidden.connect(lambda: hidden.append(True))
    for _ in range(5):
        watcher.tick()
    assert hidden == [True]


# --- the popup ------------------------------------------------------------------------------

DRAFT = RivenDraft(weapon="hikou", name="igni-insitron",
                   attrs=(RivenAttr("puncture_damage", 17.4, True), RivenAttr("heat_damage", 13.2, True),
                          RivenAttr("damage_vs_corpus", 1.07, True), RivenAttr("status_duration", -10.5, False)),
                   mr=10, rolls=0, rank=0, polarity="vazarin")


class FakeService:
    def search(self, weapon, *args, **kwargs):
        return []


def _assistant(catalog, read, opened):
    popup = RivenPopup(QRect(0, 0, 1920, 1080))
    assistant = RivenAssistant(lambda: (catalog, FakeService()), popup, lambda: None,
                               lambda draft, check: opened.append(draft), read=read)
    return popup, assistant


def test_popup_shows_the_riven_and_opens_the_window_on_click(qapp, catalog):
    opened = []
    popup, assistant = _assistant(catalog, lambda frame, cat: RivenRead(DRAFT), opened)
    assistant.card_shown(object())
    _wait(qapp, lambda: popup.draft == DRAFT and "Nobody" in popup.price_detail.text())
    assert popup.isVisible()
    assert popup.x() > 1920 // 2  # right side of the screen
    popup.clicked.emit()
    assert opened == [DRAFT] and not popup.isVisible()
    assistant.shutdown()


def test_popup_hides_with_the_card_and_drops_late_results(qapp, catalog):
    def slow_read(frame, cat):
        time.sleep(0.2)
        return RivenRead(DRAFT)

    popup, assistant = _assistant(catalog, slow_read, [])
    assistant.card_shown(object())
    assistant.card_hidden()
    time.sleep(0.4)
    qapp.processEvents()
    assert not popup.isVisible() and popup.draft is None
    assistant.shutdown()


def test_popup_says_when_the_riven_could_not_be_read(qapp, catalog):
    popup, assistant = _assistant(catalog, lambda f, c: RivenRead(RivenDraft(), ["No riven card found."]), [])
    assistant.card_shown(object())
    _wait(qapp, lambda: popup.isVisible() and popup.draft is None and popup.body.count() >= 2)
    assistant.shutdown()


def test_price_line(qapp):
    popup = RivenPopup(QRect(0, 0, 1920, 1080))
    popup.show_price(PriceCheck(20.0, 7, 150, 5, "2 of the same positive stats", ()))
    assert popup.price.text == "~20" and "5 listings" in popup.price_detail.text()


def test_reading_the_same_riven_again_changes_nothing_on_screen(qapp, catalog):
    reads = []
    popup, assistant = _assistant(catalog, lambda f, c: (reads.append(1), RivenRead(DRAFT))[1], [])
    assistant.card_shown(object())
    _wait(qapp, lambda: popup.draft == DRAFT and "Nobody" in popup.price_detail.text())
    shown_reading = []
    popup.show_reading = lambda: shown_reading.append(True)
    popup.show_read = lambda *a: shown_reading.append(True)
    assistant.card_shown(object())
    _wait(qapp, lambda: len(reads) == 2)
    qapp.processEvents()
    assert shown_reading == [] and popup.draft == DRAFT
    assistant.shutdown()


def test_a_failed_re_read_keeps_the_good_one(qapp, catalog):
    results = iter([RivenRead(DRAFT), RivenRead(RivenDraft(), ["No riven card found."])])
    popup, assistant = _assistant(catalog, lambda f, c: next(results), [])
    assistant.card_shown(object())
    _wait(qapp, lambda: popup.draft == DRAFT)
    assistant.card_shown(object())
    time.sleep(0.2)
    qapp.processEvents()
    assert popup.draft == DRAFT and popup.isVisible()
    assistant.shutdown()
