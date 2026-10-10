import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
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
    return PriceStats(slug, median, "trades_48h", 10, median, median, median, 5, 0, "ingame", time.time(), stale)


class FakePrices:
    def __init__(self):
        self.asked = []

    def get_price(self, slug):
        self.asked.append(slug)
        return stats(slug, PRICES[slug]) if slug in PRICES else None


# A blank 1080p screenshot: the settle check needs a real image, read_texts is faked anyway.
BLANK = np.zeros((1080, 1920, 3), np.uint8)


@pytest.fixture(autouse=True)
def fast_pipeline(monkeypatch):
    import voidtrace.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SETTLE_STEP_S", 0.0)
    monkeypatch.setattr(pipeline_module, "RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(pipeline_module, "SETTLE_TIMEOUT_S", 0.2)  # blank test frames never settle


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- pipeline -------------------------------------------------------------------

def test_pipeline_runs_all_phases_and_marks_best():
    prices = FakePrices()
    pipeline = RewardPipeline(
        EntityResolver(ITEMS),
        prices,
        capture=lambda: BLANK,
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
    # Name band ends at y=500 at 1080p; the squad-pick list under it takes ~110 px.
    assert all(r.top() >= 610 for r in rects)
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
    assert "slot 0: Nekros Prime Blueprint 30p (10 sold / 48h) 65d *" in text
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
        EntityResolver(ITEMS), FakePrices(), capture=lambda: BLANK,
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
        assert [c.name for c in overlay.cards] == ["Nekros Prime Blueprint", "Forma Blueprint"]

        # The game's close line hides the overlay right away.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("26.7 Script [Info]: ProjectionRewardChoice.lua: Relic reward screen shut down\n")
        deadline = time.monotonic() + 3
        while overlay.cards and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert overlay.cards == [] and not overlay.isVisible()
    finally:
        watcher.stop()
        controller.shutdown()


# --- live test 2 regressions ---------------------------------------------------------

def _load_main_app():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("main_app", Path(__file__).parent.parent / "main_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipeline_skips_when_warframe_not_in_front():
    from voidtrace.pipeline import PipelineSkipped

    captured = []
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: captured.append(1),
                              read_texts=lambda *a: ["FORMA BLUEPRINT"], foreground_check=lambda: False)
    with pytest.raises(PipelineSkipped):
        pipeline.read()
    assert captured == []  # the screen was never captured


def test_pipeline_skips_when_nothing_is_recognised():
    from voidtrace.pipeline import PipelineSkipped

    # What the end screen + terminal produced in live test 2.
    junk = ["ly Wv Wards", "Oading Item List Latching Users", "Appdata Local Warfra Script Inf"]
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: BLANK,
                              read_texts=lambda *a: junk)
    with pytest.raises(PipelineSkipped):
        pipeline.read()


def test_prices_arrive_progressively():
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: BLANK,
                              read_texts=lambda *a: ["BRATON PRIME STOCK", "NEKROS PRIME BLUEPRINT", "FORMA BLUEPRINT"])
    cards = pipeline.read()
    assert [c.pending for c in cards] == [True, True, False]  # Forma is untradable: nothing to fetch
    updates = []
    final = pipeline.price_cards(cards, on_update=updates.append)
    assert len(updates) == 2
    assert not any(c.pending for c in final)
    assert [c.best for c in final] == [False, True, False]


def test_close_during_capture_delay_cancels_capture(qapp):
    main_app = _load_main_app()
    captures = []
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: captures.append(1) or BLANK,
                              read_texts=lambda *a: ["NEKROS PRIME BLUEPRINT"])
    overlay = OverlayWindow(QRect(0, 0, 1920, 1080))
    controller = main_app.Controller(pipeline, overlay, capture_delay_ms=50)
    from voidtrace.log_watcher import RewardScreenEvent
    from datetime import datetime

    controller.on_log_event(RewardScreenEvent(datetime.now(), "Got rewards", "open"))
    controller.on_log_event(RewardScreenEvent(datetime.now(), "Relic reward screen shut down", "close"))
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    controller.shutdown()
    assert captures == [] and overlay.cards == []


# --- live test 4: capture after the fade-in ------------------------------------------------

def _fading_frame(alpha: float, seed: int) -> np.ndarray:
    """Reward band with names at `alpha` opacity over a background that changes every grab."""
    from voidtrace.cv_cleaner import reward_band, reward_slots

    rng = np.random.default_rng(seed)
    frame = rng.integers(20, 90, (1080, 1920), dtype=np.uint8)           # moving background
    frame = cv2.GaussianBlur(frame, (31, 31), 0)
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    base = frame.copy()
    for box, name in zip(reward_slots(1920, 1080, 2), ["FORMA BLUEPRINT", "NEKROS PRIME"]):
        cv2.putText(frame, name, (box.x + 10, box.y + box.h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 2)
    return cv2.addWeighted(frame, alpha, base, 1 - alpha, 0)


def test_capture_waits_until_the_name_band_stops_changing():
    # Names fading in: band brightness rises, then holds.
    alphas = [0.0, 0.25, 0.5, 0.75, 1.0, 1.0, 1.0, 1.0]
    frames = [_fading_frame(a, seed) for seed, a in enumerate(alphas)]
    grabbed = []

    def capture():
        frame = frames[min(len(grabbed), len(frames) - 1)]
        grabbed.append(frame)
        return frame

    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=capture,
                              read_texts=lambda frame, *a: ["FORMA BLUEPRINT"])
    frame = pipeline.capture_settled()
    assert any(frame is f for f in frames[5:])  # a fully faded-in frame, despite the moving background
    assert len(grabbed) >= 6


def test_second_look_replaces_a_misread_from_the_first_capture():
    # First capture caught the fade (junk), the second look reads it properly.
    reads = iter([["FORMA BLUEPRINT", "~~ ."], ["FORMA BLUEPRINT", "NEKROS PRIME BLUEPRINT"]])
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: BLANK,
                              read_texts=lambda *a: next(reads))
    cards = pipeline.read()
    assert [c.name for c in cards] == ["Forma Blueprint", "Nekros Prime Blueprint"]


def test_given_frame_gets_no_second_look():
    calls = []
    pipeline = RewardPipeline(EntityResolver(ITEMS), FakePrices(), capture=lambda: calls.append(1) or BLANK,
                              read_texts=lambda *a: ["FORMA BLUEPRINT", "~~"])
    pipeline.read(BLANK)
    assert calls == []
