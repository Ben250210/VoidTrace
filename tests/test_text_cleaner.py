import json
import time

import pytest

from voidtrace.items import Item, is_relic_reward, load_items, parse_items
from voidtrace.text_cleaner import EntityResolver, normalize

ITEMS = [
    Item("Nekros Prime Blueprint", "nekros_prime_blueprint", 65),
    Item("Nekros Prime Neuroptics Blueprint", "nekros_prime_neuroptics_blueprint", 45),
    Item("Nekros Prime Chassis Blueprint", "nekros_prime_chassis_blueprint", 15),
    Item("Nekros Prime Systems Blueprint", "nekros_prime_systems_blueprint", 100),
    Item("Akstiletto Prime Barrel", "akstiletto_prime_barrel", 45),
    Item("Akstiletto Prime Link", "akstiletto_prime_link", 15),
    Item("Braton Prime Receiver", "braton_prime_receiver", 45),
    Item("Braton Prime Stock", "braton_prime_stock", 15),
    Item("Forma Blueprint", None, 0),
]


# --- normalize ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("N3kr0s Pr1m3", "Nekros Prime"),
        ("NEKROS PRIME NEUROPTICS\nBLUEPRINT", "Nekros Prime Neuroptics Blueprint"),
        ("| AKSTILETTO PRIME\nBARREL", "Akstiletto Prime Barrel"),
        ("PRIME PRIME BRATON STOCK", "Prime Braton Stock"),
        ("  braton_prime~~receiver!! ", "Braton Prime Receiver"),
        ("FORMA 2 BLUEPRINT", "Forma Blueprint"),
        ("~~~ .. ' ", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# --- resolution -----------------------------------------------------------------

@pytest.fixture
def resolver():
    return EntityResolver(ITEMS)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("N3KR0S PR1ME\nBLUEPRlNT", "Nekros Prime Blueprint"),
        ("NEKROS PRIME NEUROPTICS BLUEPRINT", "Nekros Prime Neuroptics Blueprint"),
        ("AKSTlLETTO PRlME LINK", "Akstiletto Prime Link"),
        ("BRAT0N PRIME RECEIVER", "Braton Prime Receiver"),
        ("PRIME PRIME BRATON STOCK", "Braton Prime Stock"),
        ("FORMA BLUEPRINT", "Forma Blueprint"),
    ],
)
def test_resolves_noisy_ocr(resolver, raw, expected):
    res = resolver.resolve(raw)
    assert res.item is not None and res.item.name == expected
    assert not res.ambiguous


def test_truncated_read_resolves_but_is_flagged_ambiguous(resolver):
    res = resolver.resolve("N3kr0s Pr1m3")
    assert res.item.name == "Nekros Prime Blueprint"
    assert res.ambiguous


@pytest.mark.parametrize("raw", ["LOREM IPSUM DOLOR", "", "~~~"])
def test_rejects_garbage(resolver, raw):
    assert resolver.resolve(raw).item is None


def test_resolve_all_keeps_duplicate_slots(resolver):
    # Two players can get the same reward; each slot must stay.
    results = resolver.resolve_all(["FORMA BLUEPRINT", "F0RMA BLUEPRINT", "BRATON PRIME STOCK"])
    assert [r.item.name for r in results] == ["Forma Blueprint", "Forma Blueprint", "Braton Prime Stock"]
    assert results[1].raw == "F0RMA BLUEPRINT"


# --- master item list -----------------------------------------------------------

def _entry(name, slug, tags, ducats=0):
    return {"slug": slug, "tags": tags, "ducats": ducats, "i18n": {"en": {"name": name}}}


API_DATA = [
    _entry("Nekros Prime Blueprint", "nekros_prime_blueprint", ["blueprint", "warframe", "prime"], 65),
    _entry("Braton Prime Stock", "braton_prime_stock", ["component", "prime", "weapon"], 15),
    _entry("Nekros Prime Set", "nekros_prime_set", ["set", "warframe", "prime"], 225),
    _entry("Primed Continuity", "primed_continuity", ["mod", "legendary"]),
    _entry("Serration", "serration", ["mod", "rare"]),
]


def test_is_relic_reward_filters_sets_and_mods():
    assert [e["slug"] for e in API_DATA if is_relic_reward(e)] == ["nekros_prime_blueprint", "braton_prime_stock"]


def test_parse_items_adds_untradable_rewards():
    names = [item.name for item in parse_items(API_DATA)]
    assert names == ["Braton Prime Stock", "Forma Blueprint", "Nekros Prime Blueprint"]


class FakeClient:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, 0

    def get(self, path):
        self.calls += 1
        if self.error:
            raise self.error
        return self.data


def test_load_items_fetches_then_uses_fresh_cache(tmp_path):
    cache = tmp_path / "items.json"
    client = FakeClient(API_DATA)
    first = load_items(client, cache)
    second = load_items(client, cache)
    assert first == second
    assert client.calls == 1


def test_load_items_falls_back_to_stale_cache_when_offline(tmp_path):
    cache = tmp_path / "items.json"
    load_items(FakeClient(API_DATA), cache)
    payload = json.loads(cache.read_text())
    payload["fetched_at"] = time.time() - 10 * 24 * 3600
    cache.write_text(json.dumps(payload))
    items = load_items(FakeClient(error=ConnectionError("offline")), cache)
    assert "Nekros Prime Blueprint" in [item.name for item in items]


def test_load_items_raises_when_offline_without_cache(tmp_path):
    with pytest.raises(ConnectionError):
        load_items(FakeClient(error=ConnectionError("offline")), tmp_path / "items.json")
