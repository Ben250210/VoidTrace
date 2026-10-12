import cv2
import numpy as np
import pytest
from pathlib import Path

from voidtrace.cv_cleaner import (
    Box,
    crop,
    detect_player_count,
    find_tesseract,
    ocr,
    preprocess,
    read_reward_texts,
    reward_band,
    reward_slots,
    screen_scaling,
    slot_centers,
)


def synthetic_reward_screen(names, width=1920, height=1080, light_theme=False, seed=0):
    """Dark gradient + noise + a bloom blob, with reward names drawn at the card centers."""
    rng = np.random.default_rng(seed)
    gradient = np.linspace(15, 70, width, dtype=np.float32)[None, :].repeat(height, axis=0)
    frame = gradient + rng.normal(0, 6, (height, width)).astype(np.float32)
    band = reward_band(width, height)
    # Bloom/particle glow sitting behind the text row.
    cv2.circle(frame, (band.x + band.w // 2, band.y + band.h // 2), 60, 95, -1)
    frame = cv2.GaussianBlur(frame, (41, 41), 0)
    if light_theme:
        frame = 255 - frame
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    color = (30, 30, 30) if light_theme else (235, 235, 235)
    scale = screen_scaling(width, height)
    for cx, name in zip(slot_centers(band, len(names)), names):
        (tw, th), _ = cv2.getTextSize(name, cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, 2)
        org = (cx - tw // 2, band.y + band.h // 2 + th // 2)
        cv2.putText(frame, name, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, color, 2, cv2.LINE_AA)
    return frame


# --- geometry ---------------------------------------------------------------

@pytest.mark.parametrize(
    "size,expected",
    [
        ((1920, 1080), 1.0),
        ((2560, 1440), 1440 / 1080),
        ((3440, 1440), 1440 / 1080),  # ultrawide: height-driven
        ((1920, 1200), 1.0),           # 16:10: width-driven
    ],
)
def test_screen_scaling(size, expected):
    assert screen_scaling(*size) == pytest.approx(expected)


def test_reward_band_at_1080p_matches_reference():
    assert reward_band(1920, 1080) == Box(476, 411, 968, 89)


def test_reward_band_scales_to_1440p():
    band = reward_band(2560, 1440)
    assert band.w == int(968 * 1440 / 1080)
    assert band.x + band.w // 2 == pytest.approx(1280, abs=1)


def test_reward_band_centered_on_ultrawide():
    band = reward_band(3440, 1440)
    assert band.x + band.w // 2 == pytest.approx(1720, abs=1)


@pytest.mark.parametrize(
    "players,expected",
    [
        (4, [597, 839, 1081, 1323]),
        (3, [718, 960, 1202]),
        (2, [839, 1081]),
        (1, [960]),
    ],
)
def test_slot_centers_at_1080p(players, expected):
    assert slot_centers(reward_band(1920, 1080), players) == expected


def test_slot_centers_rejects_bad_player_count():
    with pytest.raises(ValueError):
        slot_centers(reward_band(1920, 1080), 5)


def test_reward_slots_have_card_width():
    slots = reward_slots(1920, 1080, 4)
    assert len(slots) == 4
    assert all(s.w == 242 for s in slots)


def test_crop_clamps_to_frame():
    frame = np.zeros((100, 100, 3), np.uint8)
    assert crop(frame, Box(-10, 90, 50, 50)).shape == (10, 40, 3)


# --- cleaning ---------------------------------------------------------------

@pytest.mark.parametrize("light_theme", [False, True])
def test_preprocess_yields_dark_text_on_white(light_theme):
    frame = synthetic_reward_screen(["NEKROS PRIME"], light_theme=light_theme)
    slot = reward_slots(1920, 1080, 1)[0]
    clean = preprocess(crop(frame, slot))
    assert set(np.unique(clean)) <= {0, 255}
    black_ratio = np.count_nonzero(clean == 0) / clean.size
    assert 0.02 < black_ratio < 0.4  # some text, mostly background


def test_preprocess_removes_bloom_behind_text():
    # Crop a text-free part of the bloom: after Otsu it should be pure background.
    frame = synthetic_reward_screen(["NEKROS PRIME", "AKSTILETTO PRIME"])
    band = reward_band(1920, 1080)
    mid_gap = Box(band.x + band.w // 2 - 15, band.y, 30, band.h)
    clean = preprocess(np.concatenate([crop(frame, mid_gap), crop(frame, reward_slots(1920, 1080, 2)[0])], axis=1))
    gap_cols = clean[:, 10:10 + 30 * 2]  # pad 10, upscale 2x
    assert np.count_nonzero(gap_cols == 0) / gap_cols.size < 0.01


@pytest.mark.parametrize("players", [1, 2, 3, 4])
def test_detect_player_count(players):
    names = ["NEKROS PRIME", "FORMA BLUEPRINT", "AKSTILETTO PRIME", "BRATON PRIME"][:players]
    frame = synthetic_reward_screen(names, seed=players)
    assert detect_player_count(crop(frame, reward_band(1920, 1080))) == players


@pytest.mark.parametrize("size", [(2560, 1440), (3440, 1440)])
def test_detect_player_count_other_resolutions(size):
    frame = synthetic_reward_screen(["NEKROS PRIME", "FORMA BLUEPRINT", "BRATON PRIME"], *size)
    assert detect_player_count(crop(frame, reward_band(*size))) == 3


def test_read_reward_texts_runs_one_ocr_per_detected_card(tmp_path):
    frame = synthetic_reward_screen(["NEKROS PRIME", "FORMA BLUEPRINT", "BRATON PRIME"])
    calls = []
    texts = read_reward_texts(frame, ocr_fn=lambda img: calls.append(img) or "x", debug_dir=tmp_path)
    assert texts == ["x", "x", "x"]
    assert len(list(tmp_path.glob("slot*_clean.png"))) == 3


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
def test_ocr_reads_synthetic_name():
    frame = synthetic_reward_screen(["NEKROS PRIME"])
    clean = preprocess(crop(frame, reward_slots(1920, 1080, 1)[0]))
    assert "NEKROS" in ocr(clean).upper()


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
def test_cleaning_reads_harsh_screen_with_bloom_panel_and_particles():
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo", Path(__file__).resolve().parents[1] / "tools" / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    REWARDS, harsh_reward_screen = demo.REWARDS, demo.harsh_reward_screen
    from voidtrace.items import Item
    from voidtrace.text_cleaner import EntityResolver

    items = [Item(name.title(), None, 0) for name in REWARDS] + [
        Item("Nekros Prime Chassis Blueprint", None, 0),
        Item("Akstiletto Prime Link", None, 0),
        Item("Saryn Prime Neuroptics Blueprint", None, 0),
    ]
    resolver = EntityResolver(items)
    frame = harsh_reward_screen(REWARDS)
    texts = read_reward_texts(frame)
    assert [r.item.name if r.item else None for r in resolver.resolve_all(texts)] == [n.title() for n in REWARDS]
