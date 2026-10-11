"""Reading rivens off real Item Details screens (2026-10-11, 2560x1600, the user's own rivens).

Only the card area is stored. Each card is put back on a blank frame of the original screenshot
size and read through the same path Ctrl+Shift+R uses.
"""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from voidtrace import rivens
from voidtrace.cv_cleaner import find_tesseract
from voidtrace.riven_ocr import parse_stat_lines, read_riven
from voidtrace.rivens import RivenAttr

FIXTURES = Path(__file__).parent / "fixtures" / "rivens"
SCREENS = FIXTURES / "screens"

# What each card says in game, typed from the screenshots.
EXPECTED = {
    "sobek_satidra": ("sobek", "satidra", 10, 238, 8,
                      [("multishot", 170.4, True), ("fire_rate_/_attack_speed", 121.9, True)]),
    "burston_sati-visicon": ("burston", "sati-visicon", 9, 3, 8,
                             [("damage_vs_grineer", 1.47, True), ("base_damage_/_melee_damage", 174.7, True),
                              ("multishot", 96.8, True)]),
    "hikou_igni-insitron": ("hikou", "igni-insitron", 10, 0, 0,
                            [("puncture_damage", 17.4, True), ("heat_damage", 13.2, True),
                             ("damage_vs_corpus", 1.07, True), ("status_duration", -10.5, False)]),
    "balla_acriata": ("balla", "acriata", 11, 32, 8,
                      [("base_damage_/_melee_damage", 164.0, True), ("critical_damage", 91.7, True),
                       ("fire_rate_/_attack_speed", -22.0, False)]),
    "dread_manti-vexiata": ("dread", "manti-vexiata", 16, 0, 0,
                            [("electric_damage", 9.5, True), ("damage_vs_corpus", 1.05, True),
                             ("base_damage_/_melee_damage", 16.2, True)]),
    "balla_acrido": ("balla", "acrido", 12, 22, 8,
                     [("cold_damage", 82.3, True), ("critical_damage", 84.9, True), ("status_chance", -33.9, False)]),
    "ceramic_dagger_manti-parades": ("ceramic_dagger", "manti-parades", 16, 0, 8,
                                     [("damage_vs_corpus", 1.66, True), ("status_duration", 122.7, True),
                                      ("channeling_damage", 33.3, True), ("status_chance", -95.5, False)]),
    # The same Burston, grabbed live with Ctrl+Shift+R (14:56): "+174.7%" was read as 14.0 there.
    "burston_live": ("burston", "sati-visicon", 9, 3, 8,
                     [("damage_vs_grineer", 1.47, True), ("base_damage_/_melee_damage", 174.7, True),
                      ("multishot", 96.8, True)]),
    # Inventory > Mods > Cycle (15:39-15:41): bigger card, rolls from "CYCLE FOR 900".
    "efv_8_mars_veximag": ("efv_8_mars", "veximag", 9, 0, 8,
                           [("electric_damage", 94.4, True), ("recoil", -92.8, True)]),
    "euphona_prime_croni-vexitron": ("euphona_prime", "croni-vexitron", 11, 0, 8,
                                     [("fire_rate_/_attack_speed", 49.7, True), ("damage_vs_corpus", 1.3, True),
                                      ("electric_damage", 59.6, True)]),
    "akvasto_acri-decitin": ("akvasto", "acri-decitin", 16, 0, 8,
                             [("status_duration", 87.6, True), ("magazine_capacity", 42.4, True),
                              ("critical_damage", 87.5, True)]),
    # Popup live test (16:07): two-line title and four stats sit higher; 3 rolls ("CYCLE FOR 1,400").
    "akmagnus_sci-critaata": ("akmagnus", "sci-critaata", 11, 3, 8,
                              [("base_damage_/_melee_damage", 290.6, True), ("slash_damage", 162.3, True),
                               ("critical_chance", 200.0, True), ("puncture_damage", -126.0, False)]),
}

POLARITY = {"sobek_satidra": "madurai", "burston_sati-visicon": "naramon", "hikou_igni-insitron": "vazarin",
            "balla_acriata": "madurai", "dread_manti-vexiata": "madurai", "balla_acrido": "naramon",
            "ceramic_dagger_manti-parades": "madurai", "burston_live": "naramon",
            "efv_8_mars_veximag": "madurai", "euphona_prime_croni-vexitron": "vazarin",
            "akvasto_acri-decitin": "vazarin", "akmagnus_sci-critaata": "madurai"}


@pytest.fixture(scope="module")
def catalog():
    return rivens.RivenCatalog(rivens.parse_weapons(json.loads((FIXTURES / "weapons.json").read_text())["data"]),
                               rivens.parse_stats(json.loads((FIXTURES / "attributes.json").read_text())["data"]))


def screen(key: str) -> np.ndarray:
    meta = json.loads((SCREENS / "screens.json").read_text())[key]
    card = cv2.imread(str(SCREENS / f"{key}.png"))
    w, h = meta["screen"]
    x, y = meta["offset"]
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[y:y + card.shape[0], x:x + card.shape[1]] = card
    return frame


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
@pytest.mark.parametrize("key", list(EXPECTED))
def test_reads_real_riven_cards(key, catalog):
    weapon, name, mr, rolls, rank, attrs = EXPECTED[key]
    read = read_riven(screen(key), catalog)
    assert read.warnings == []
    d = read.draft
    assert (d.weapon, d.name, d.mr, d.rolls, d.rank) == (weapon, name, mr, rolls, rank)
    assert list(d.attrs) == [RivenAttr(*a) for a in attrs]
    assert d.polarity == POLARITY[key]


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
def test_no_card_on_screen(catalog):
    read = read_riven(np.zeros((1600, 2560, 3), dtype=np.uint8), catalog)
    assert not read.ok and "No riven card" in read.warnings[0]


def test_stat_line_parsing_survives_ocr_slips():
    lines = ["\\ 7% *", "Burston Sati-visicon", "x1.47 Damage to Grineer", "+1/74.7% Damage",
             "3  +96.8%Multishot  ,", "+121.9% Fire Rate (x2 for", "Bows)", "+13.2% AHeat", "MR 9 _ 93"]
    first, stats = parse_stat_lines(lines)
    assert first == 2
    assert [(s, v, label) for s, v, _, label, _alts in stats] == [
        ("x", 1.47, "Damage to Grineer"), ("+", 174.7, "Damage"), ("+", 96.8, "Multishot"),
        ("+", 121.9, "Fire Rate Bows)"), ("+", 13.2, "Heat")]  # matched as Fire Rate later


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
@pytest.mark.parametrize("size, allowed_misses", [((1920, 1200), 0), ((1680, 1050), 1)])
def test_reads_the_same_cards_on_smaller_screens(size, allowed_misses, catalog):
    # Downscaled screenshots are a harsher test than the game rendering natively at that size.
    # At 1680x1050 the live Burston loses its MR, rolls and a 6 (96.8 -> 90.8): 11 of 12 exact.
    misses = []
    for key, (weapon, name, mr, rolls, rank, attrs) in EXPECTED.items():
        d = read_riven(cv2.resize(screen(key), size, interpolation=cv2.INTER_AREA), catalog).draft
        read = (d.weapon, d.name, d.mr, d.rolls, d.rank, d.polarity, list(d.attrs))
        if read != (weapon, name, mr, rolls, rank, POLARITY[key], [RivenAttr(*a) for a in attrs]):
            misses.append(key)
    assert len(misses) <= allowed_misses, misses


def test_implausible_values(catalog):
    from voidtrace.riven_ocr import plausible

    assert plausible(catalog, "multishot", 96.8) and not plausible(catalog, "multishot", 968.0)
    assert plausible(catalog, "damage_vs_corpus", 1.07) and not plausible(catalog, "damage_vs_corpus", 107.0)


def test_slash_read_for_a_seven():
    from voidtrace.riven_ocr import number_variants

    assert 174.7 in number_variants("+1/4./% Damage")      # every 7 read as "/"
    assert 174.7 in number_variants("+1/74.7/% Damage")    # a stray extra "/"
    assert number_variants("x1.47 Damage to Grineer") == [1.47]


def test_rolls_from_the_cycle_price():
    from voidtrace.riven_ocr import rolls_from_cost

    assert rolls_from_cost("CYCLE FOR ©-..900") == 0
    assert rolls_from_cost("CYCLE FOR 1,400") == 3
    assert rolls_from_cost("CYCLE FOR 3500") == 9  # 9 or more: the price stops rising
    assert rolls_from_cost("CYCLE FOR") is None
