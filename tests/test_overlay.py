import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from voidtrace.items import Item
from voidtrace.overlay import OverlayWindow, cards_summary
from voidtrace.pipeline import RewardCard, RewardPipeline, mark_best
from voidtrace.price_cleaner import PriceStats
from voidtrace.text_cleaner import EntityResolver, Resolution

ITEMS = [
    Item("Nekros Prime Blueprint", "nekros_prime_blueprint", 65),
    Item("Braton Prime Stock", "braton_prime_stock", 15),
    Item("Forma Blueprint", None, 0),
]
PRICES = {"nekros_prime_blueprint": 30.0, "braton_prime_stock": 4.0}


def stats(slug, median, stale=False):
    return PriceStats(slug, median, median, median, median, median, 5, 5, 0, "ingame", time.time(), stale)


class FakePrices:
    def __init__(self):
        self.asked = []

    def get_price(self, slug):
        self.asked.append(slug)
        return stats(slug, PRICES[slug]) if slug in PRICES else None


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- pipeline -------------------------------------------------------------------

def test_pipeline_runs_all_phases_and_marks_best():
    prices = FakePrices()
    pipeline = RewardPipeline(
        EntityResolver(ITEMS),
        prices,
        capture=lambda: "frame",
        read_texts=lambda frame, players, ui_scale: ["BRAT0N PRIME STOCK", "NEKROS PRIME BLUEPRINT", "FORMA BLUEPRINT", "~~"],
    )
    cards = pipeline.run()
    assert [c.name for c in cards] == ["Braton Prime Stock", "Nekros Prime Blueprint", "Forma Blueprint", None]
    assert [c.best for c in cards] == [False, True, False, False]
    assert prices.asked == ["braton_prime_stock", "nekros_prime_blueprint"]  # untradable not queried


def test_mark_best_breaks_plat_ties_on_ducats():
    a = RewardCard(0, Resolution("", "", ITEMS[1], 100), stats("braton_prime_stock", 30))
    b = RewardCard(1, Resolution("", "", ITEMS[0], 100), stats("nekros_prime_blueprint", 30))
    assert [c.best for c in mark_best([a, b])] == [False, True]


def test_mark_best_with_nothing_readable():
    card = RewardCard(0, Resolution("~~", "", None, 0), None)
    assert mark_best([card]) == [card]


# --- overlay ---------------------------------------------------------------------

def sample_cards():
    res = EntityResolver(ITEMS)
    return mark_best([
        RewardCard(0, res.resolve("NEKROS PRIME BLUEPRINT"), stats("nekros_prime_blueprint", 30)),
        RewardCard(1, res.resolve("N3kr0s Pr1m3"), stats("nekros_prime_blueprint", 30, stale=True)),
        RewardCard(2, res.resolve("FORMA BLUEPRINT"), None),
        RewardCard(3, res.resolve("~~~"), None),
    ])


def test_overlay_cards_sit_below_reward_names_at_1080p(qapp):
    overlay = OverlayWindow(QRect(0, 0, 1920, 1080))
    rects = overlay.layout_cards(4)
    assert len(rects) == 4
    assert all(r.top() >= 500 for r in rects)          # band bottom is y=500 at 1080p
    centers = [r.center().x() for r in rects]
    assert centers == pytest.approx([597, 839, 1081, 1323], abs=2)


def test_overlay_layout_respects_display_scaling(qapp):
    # 1440p panel at 150% Windows scaling: Qt reports 1707x960 logical pixels.
    overlay = OverlayWindow(QRect(0, 0, 1707, 960), device_pixel_ratio=1.5)
    rects = overlay.layout_cards(1)
    assert rects[0].center().x() == pytest.approx(1707 / 2, abs=2)


def test_overlay_paints_every_card_state(qapp):
    overlay = OverlayWindow(QRect(0, 0, 1920, 1080), hide_after_ms=50)
    overlay.show_cards(sample_cards())
    image = overlay.grab()  # forces a paintEvent
    assert not image.isNull()
    assert overlay.isVisible()


def test_overlay_auto_hides(qapp):
    overlay = OverlayWindow(QRect(0, 0, 1920, 1080), hide_after_ms=20)
    overlay.show_cards(sample_cards())
    deadline = time.monotonic() + 2
    while overlay.isVisible() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not overlay.isVisible() and overlay.cards == []


def test_overlay_is_click_through(qapp):
    from PyQt6.QtCore import Qt

    flags = OverlayWindow(QRect(0, 0, 100, 100)).windowFlags()
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_cards_summary_lists_each_slot():
    text = cards_summary(sample_cards())
    assert "slot 0: Nekros Prime Blueprint 30p 65d *" in text
    assert "slot 3: <unreadable>" in text


# --- end to end: log line -> overlay ------------------------------------------------------

def test_log_trigger_drives_overlay(qapp, tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("main_app", Path(__file__).parent.parent / "main_app.py")
    main_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_app)

    from voidtrace.log_watcher import LogWatcher

    pipeline = RewardPipeline(
        EntityResolver(ITEMS), FakePrices(), capture=lambda: "frame",
        read_texts=lambda frame, players, ui_scale: ["NEKROS PRIME BLUEPRINT", "FORMA BLUEPRINT"],
    )
    overlay = OverlayWindow(QRect(0, 0, 1920, 1080))
    controller = main_app.Controller(pipeline, overlay, capture_delay_ms=0)

    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    watcher = LogWatcher(log, on_event=controller.on_log_event, poll_interval=0.01)
    watcher.start()
    try:
        time.sleep(0.05)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("12.4 Script [Info]: ProjectionRewardChoice.lua: Got rewards\n")
        deadline = time.monotonic() + 3
        while not overlay.cards and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
    finally:
        watcher.stop()
        controller.shutdown()
    assert [c.name for c in overlay.cards] == ["Nekros Prime Blueprint", "Forma Blueprint"]
