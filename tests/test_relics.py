import itertools

import pytest

from voidtrace.relics import (CHANCE, REFINEMENTS, Relic, Reward, chance, expected_best, load_relics, relic_value,
                              search_relics)

RELIC = Relic("Axi Z9 Relic", False, (
    Reward("C1", "common"), Reward("C2", "common"), Reward("C3", "common"),
    Reward("U1", "uncommon"), Reward("U2", "uncommon"), Reward("R", "rare"),
))
PRICES = {"C1": 2, "C2": 3, "C3": 5, "U1": 10, "U2": 15, "R": 100}


@pytest.mark.parametrize("refinement", REFINEMENTS)
def test_chances_add_to_100(refinement):
    c = CHANCE[refinement]
    assert 3 * c["common"] + 2 * c["uncommon"] + c["rare"] == pytest.approx(1.0, abs=0.001)


def test_solo_value_is_the_weighted_average():
    value, missing = relic_value(RELIC, lambda r: PRICES[r.name], "intact")
    expected = (2 + 3 + 5) * 0.2533 + (10 + 15) * 0.11 + 100 * 0.02
    assert missing == 0 and value == pytest.approx(expected, rel=1e-3)
    radiant, _ = relic_value(RELIC, lambda r: PRICES[r.name], "radiant")
    assert radiant > value


def brute_force_best(rows, picks):
    total = 0.0
    for combo in itertools.product(rows, repeat=picks):
        p = 1.0
        for _, chance_ in combo:
            p *= chance_
        total += p * max(v for v, _ in combo)
    return total / sum(c for _, c in rows) ** picks


@pytest.mark.parametrize("refinement", ["intact", "radiant"])
def test_best_of_four_matches_brute_force(refinement):
    rows = [(PRICES[r.name], chance(r.rarity, refinement)) for r in RELIC.rewards]
    assert expected_best(rows, 4) == pytest.approx(brute_force_best(rows, 4), rel=1e-6)
    assert expected_best(rows, 4) > expected_best(rows, 1)


def test_unknown_prices_count_as_zero_and_are_reported():
    value, missing = relic_value(RELIC, lambda r: None if r.name == "R" else PRICES[r.name], "radiant")
    assert missing == 1
    assert value == pytest.approx((2 + 3 + 5) * 0.1667 + (10 + 15) * 0.20, rel=1e-3)


def test_count_multiplies():
    relic = Relic("Lith X1 Relic", True, (Reward("Forma", "common", 2),) * 3 + (Reward("U", "uncommon"),) * 2
                  + (Reward("R", "rare"),))
    value, _ = relic_value(relic, lambda r: 10.0 if r.name == "Forma" else 0.0, "intact")
    assert value == pytest.approx(3 * 0.2533 * 20, rel=1e-3)


def test_bundled_data():
    relics = load_relics()
    assert len(relics) > 500
    assert [r.era for r in relics[:1]] == ["Lith"] and relics[-1].era == "Axi"
    axi = next(r for r in relics if r.name == "Axi A1 Relic")
    assert sorted(r.rarity for r in axi.rewards) == ["common"] * 3 + ["rare"] + ["uncommon"] * 2
    assert "Nikana Prime Blueprint" in {r.name for r in axi.rewards}
    assert all(len(r.rewards) == 6 for r in relics)
    assert any(r.vaulted is False for r in relics)


def test_search():
    relics = load_relics()
    assert [r.name for r in search_relics("axi a1", relics)][:1] == ["Axi A1 Relic"]
    assert all("Nikana Prime" in " ".join(x.name for x in r.rewards)
               for r in search_relics("nikana prime", relics))
    assert search_relics("  ", relics) == []
