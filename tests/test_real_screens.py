"""Regression test on the name bands of five real relic reward screens (2026-10-10, 2560x1600).

Only the reward-name band is stored (no player names). Each band is placed back on a blank frame
of the original screen size and run through the live OCR + matching path.
"""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from voidtrace.cv_cleaner import find_tesseract, read_reward_texts, reward_band
from voidtrace.items import Item
from voidtrace.text_cleaner import EntityResolver

FIXTURES = Path(__file__).parent / "fixtures"
BANDS = FIXTURES / "real_bands"

pytestmark = pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")


@pytest.fixture(scope="module")
def resolver():
    items = json.loads((FIXTURES / "relic_rewards_2026-10-10.json").read_text(encoding="utf-8"))
    return EntityResolver(Item(**item) for item in items)


def read_screen(key: str, screen: list[int]):
    w, h = screen
    frame = np.zeros((h, w, 3), np.uint8)
    band = reward_band(w, h)
    img = cv2.imread(str(BANDS / f"{key}.png"))
    frame[band.y:band.y + img.shape[0], band.x:band.x + img.shape[1]] = img
    return read_reward_texts(frame)


def test_real_screens(resolver):
    manifest = json.loads((BANDS / "truth.json").read_text(encoding="utf-8"))
    correct, total, unsafe = 0, 0, []
    for key, case in manifest.items():
        texts = read_screen(key, case["screen"])
        assert len(texts) == 4, f"{key}: squad size detected as {len(texts)}"
        for res, truth in zip(resolver.resolve_all(texts), case["truth"]):
            total += 1
            if res.item is not None and res.item.name == truth:
                correct += 1
            elif res.item is not None and not res.ambiguous:
                unsafe.append((key, truth, res.item.name, res.score))
    # Kompressa Prime Blueprint (two-line name with the pistol art over it) is the known hard case.
    assert correct >= total - 1, f"{correct}/{total} correct"
    # A wrong answer may happen, but never presented as certain.
    assert unsafe == []
