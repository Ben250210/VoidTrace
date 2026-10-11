import json
import time
from pathlib import Path

import pytest

from voidtrace import rivens
from voidtrace.rivens import RivenAttr, RivenAuction, RivenService, parse_auction, price_check

FIXTURES = Path(__file__).parent / "fixtures" / "rivens"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return rivens.RivenCatalog(rivens.parse_weapons(_load("weapons.json")["data"]),
                               rivens.parse_stats(_load("attributes.json")["data"]))


@pytest.fixture(scope="module")
def kulstar_auctions():
    # Real search result (2026-10-11), seller names replaced.
    return [parse_auction(a) for a in _load("auctions_kulstar.json")["payload"]["auctions"]]


def auction(price, positives, negative=None, seller="Someone", status="ingame", buyout=True):
    attrs = [RivenAttr(s, 100.0, True) for s in positives]
    if negative:
        attrs.append(RivenAttr(negative, -20.0, False))
    return RivenAuction(id=f"{seller}{price}", weapon="kulstar", name="test", attrs=tuple(attrs), mr=8, rolls=0,
                        rank=0, polarity="madurai", buyout=price if buyout else None, starting=price,
                        top_bid=None, direct=buyout, seller=seller, status=status, reputation=0)


def test_weapon_and_stat_lists(catalog):
    kulstar = catalog.by_weapon["kulstar"]
    assert (kulstar.name, kulstar.riven_type, kulstar.group) == ("Kulstar", "pistol", "secondary")
    assert kulstar.disposition > 0
    assert len(catalog.stats) == 50


def test_stats_are_filtered_per_weapon(catalog):
    pistol = {s.slug for s in catalog.stats_for(catalog.by_weapon["kulstar"])}
    melee = {s.slug for s in catalog.stats_for(catalog.by_weapon["nikana"])}
    assert "multishot" in pistol and "multishot" not in melee
    assert "range" in melee and "range" not in pistol
    assert "critical_chance" in pistol and "critical_chance" in melee


def test_parse_real_auction(kulstar_auctions, catalog):
    first = kulstar_auctions[0]
    assert first.weapon == "kulstar" and first.attrs
    assert first.price is not None
    assert catalog.riven_title(first).startswith("Kulstar ")


def test_format_attr_units(catalog):
    assert catalog.format_attr(RivenAttr("critical_chance", 108.2, True)) == "+108.2% Critical Chance"
    assert catalog.format_attr(RivenAttr("damage_vs_corpus", 1.08, True)) == "x1.08 Damage to Corpus"
    assert catalog.format_attr(RivenAttr("zoom", -6.0, False)) == "-6% Zoom"


def test_search_path():
    path = rivens.search_path("kulstar", ["critical_chance", "multishot"], "none", buyout_only=True)
    assert path.startswith("/auctions/search?type=riven&weapon_url_name=kulstar")
    assert "positive_stats=critical_chance%2Cmultishot" in path
    assert "negative_stats=none" in path and "buyout_policy=direct" in path


def test_whisper():
    a = auction(120, ["critical_chance"], seller="Kosmo")
    assert rivens.riven_whisper(a, "Ocucor Sati-toxitio") == (
        "/w Kosmo Hi! I'd like to buy your Ocucor Sati-toxitio riven for 120 platinum. "
        "(warframe.market through VoidTrace)")


def test_whisper_for_bid_only_auction():
    a = RivenAuction(id="x", weapon="kulstar", name="test", attrs=(), mr=8, rolls=0, rank=0, polarity="madurai",
                     buyout=None, starting=0, top_bid=None, direct=False, seller="Kosmo", status="ingame",
                     reputation=0)
    a = RivenAuction(**{**a.__dict__, "starting": None})
    assert rivens.riven_whisper(a, "Kulstar Test") == \
        "/w Kosmo Hi! I'm interested in your Kulstar Test riven. (warframe.market through VoidTrace)"


def test_riven_title_keeps_the_game_casing(catalog):
    a = auction(10, ["critical_chance"])
    a = RivenAuction(**{**a.__dict__, "name": "sati-toxitio"})
    assert catalog.riven_title(a) == "Kulstar Sati-toxitio"


def test_sort_puts_tradeable_sellers_first():
    rows = rivens.sort_auctions([auction(10, [], status="offline"), auction(50, [], status="online"),
                                 auction(30, [], status="ingame")])
    assert [r.price for r in rows] == [30, 50, 10]


def test_price_check_uses_the_most_alike_tier_with_enough_listings():
    pool = [auction(p, ["critical_chance", "multishot"], "zoom") for p in (100, 110, 120, 130)]
    pool += [auction(p, ["critical_chance", "multishot"]) for p in (300, 320)]
    pool += [auction(p, ["status_chance"]) for p in (5, 6, 7, 8)]
    check = price_check(pool, ["critical_chance", "multishot"], "zoom")
    assert check.tier == "same stats and curse"
    assert check.count == 4 and check.estimate == 115
    assert (check.low, check.high) == (100, 130)


def test_price_check_falls_back_when_too_few_are_alike():
    pool = [auction(p, ["critical_chance", "multishot"], "zoom") for p in (100, 110)]
    pool += [auction(p, ["critical_chance", "multishot"]) for p in (90, 95)]
    check = price_check(pool, ["critical_chance", "multishot"], "zoom")
    assert check.tier == "same positive stats" and check.count == 4


def test_price_check_drops_outliers_and_ignores_my_own_listing():
    pool = [auction(p, ["critical_chance"]) for p in (40, 45, 50, 55, 60, 5000)]
    pool.append(auction(1, ["critical_chance"], seller="Me"))
    check = price_check(pool, ["critical_chance"], None, exclude_seller="me")
    assert 5000 in check.outliers
    assert all(a.seller != "Me" for a in check.comparables)
    assert check.estimate == 50


def test_price_check_on_real_listings(kulstar_auctions):
    check = price_check(kulstar_auctions, ["critical_chance", "multishot"], None)
    assert check.estimate is not None and check.count >= 1
    assert check.low <= check.estimate <= check.high


def test_price_check_with_nothing():
    assert price_check([], ["critical_chance"]).estimate is None


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_v1(self, path):
        self.calls.append(path)
        return self.payload

    def get(self, path):
        self.calls.append(path)
        return _load("weapons.json")["data"] if "weapons" in path else _load("attributes.json")["data"]


def test_service_caches_and_filters_status():
    client = FakeClient(_load("auctions_kulstar.json")["payload"])
    service = RivenService(client)
    ingame = service.search("kulstar", statuses=("ingame",))
    everyone = service.search("kulstar")
    assert len(client.calls) == 1
    assert ingame and all(a.status == "ingame" for a in ingame)
    assert len(everyone) > len(ingame)


def test_catalog_cache_round_trip(tmp_path):
    client = FakeClient({})
    path = tmp_path / "rivens.json"
    first = rivens.load_riven_catalog(client, path)
    second = rivens.load_riven_catalog(client, path)
    assert len(client.calls) == 2  # weapons + attributes once; second load reads the cache
    assert [w.slug for w in first.weapons] == [w.slug for w in second.weapons]


def test_catalog_uses_stale_cache_when_offline(tmp_path):
    path = tmp_path / "rivens.json"
    rivens.load_riven_catalog(FakeClient({}), path)
    raw = json.loads(path.read_text())
    raw["fetched_at"] = time.time() - 10 * rivens.CACHE_TTL
    path.write_text(json.dumps(raw))

    class Offline:
        def get(self, path):
            raise ConnectionError("offline")

    assert rivens.load_riven_catalog(Offline(), path).weapons


def test_price_check_still_lists_the_few_exact_matches():
    pool = [auction(400, ["critical_chance", "multishot"], "zoom")]
    pool += [auction(p, ["critical_chance", "status_chance"]) for p in (50, 60, 70, 80)]
    check = price_check(pool, ["critical_chance", "multishot"], "zoom")
    assert check.tier == "1 of the same positive stats"
    assert [a.price for a in check.exact] == [400]


def test_buffs_from_name(catalog):
    nikana = catalog.by_weapon["nikana"]
    assert rivens.buffs_from_name(catalog, "Critacan") == [["critical_chance"], ["multishot"]]
    assert rivens.buffs_from_name(catalog, "critacron") == [["critical_chance"]]
    assert rivens.buffs_from_name(catalog, "sci-critaata") == [["slash_damage"], ["critical_chance"],
                                                               ["base_damage_/_melee_damage"]]
    assert rivens.buffs_from_name(catalog, "nonsense") is None
    # Laci + Nus is shared by several melee stats: all candidates are offered.
    assert len(rivens.buffs_from_name(catalog, "lacinus", nikana)[0]) > 1


def test_buffs_from_name_round_trips_valid_names(catalog):
    for positives in (["critical_chance", "multishot"], ["heat_damage", "puncture_damage", "damage_vs_corpus"]):
        for name in rivens.valid_names(catalog, positives):
            slots = rivens.buffs_from_name(catalog, name)
            assert sorted(s[0] for s in slots) == sorted(positives), name
