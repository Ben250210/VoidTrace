import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.hotkey import HOTKEY_ID, WM_HOTKEY, HotkeyFilter
from voidtrace.items import CatalogItem, load_catalog, load_items
from voidtrace.market_window import MarketWindow, search_catalog
from voidtrace.sellers import SellerOrder, SellerService, parse_sellers, whisper_text


def order(name, plat, status="ingame", rank=None, qty=1, rep=0, type_="sell", visible=True):
    o = {"type": type_, "platinum": plat, "quantity": qty, "visible": visible, "updatedAt": "2026-10-10T20:00:00Z",
         "user": {"ingameName": name, "status": status, "reputation": rep}}
    if rank is not None:
        o["rank"] = rank
    return o


CATALOG = [
    CatalogItem("Primed Continuity", "primed_continuity", 10, ("mod", "legendary")),
    CatalogItem("Primed Flow", "primed_flow", 10, ("mod", "legendary")),
    CatalogItem("Continuity", "continuity", 5, ("mod",)),
    CatalogItem("Burston Prime Blueprint", "burston_prime_blueprint", None, ("prime", "blueprint")),
    CatalogItem("Burston Prime Barrel", "burston_prime_barrel", None, ("prime", "component")),
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- whisper ---------------------------------------------------------------------

def test_whisper_for_unranked_item():
    s = SellerOrder("Kosmo", "ingame", 5, 1, None, 12, "")
    assert whisper_text(s, "Burston Prime Blueprint") == \
        '/w Kosmo Hi! I want to buy: "Burston Prime Blueprint" for 5 platinum. (warframe.market)'


def test_whisper_for_ranked_mod_includes_rank():
    s = SellerOrder("Ayla_09", "online", 45, 3, 10, 49, "")
    assert whisper_text(s, "Primed Continuity", ranked=True) == \
        '/w Ayla_09 Hi! I want to buy: "Primed Continuity (rank 10)" for 45 platinum. (warframe.market)'


def test_whisper_rank_zero_is_still_written():
    s = SellerOrder("x", "ingame", 40, 1, 0, 0, "")
    assert '"Primed Continuity (rank 0)"' in whisper_text(s, "Primed Continuity", ranked=True)


# --- seller list -------------------------------------------------------------------

def test_sellers_drop_offline_buys_and_hidden_and_sort():
    orders = [
        order("Off", 1, status="offline"), order("Buyer", 99, type_="buy"), order("Hidden", 2, visible=False),
        order("Online", 4, status="online"), order("CheapGame", 5), order("Game", 5, rep=40), order("Pricier", 6),
    ]
    rows = parse_sellers(orders)
    assert [r.ingame_name for r in rows] == ["Game", "CheapGame", "Pricier", "Online"]  # in game first, then price, rep


def test_sellers_online_toggle():
    rows = parse_sellers([order("A", 5), order("B", 4, status="online")], include_online=False)
    assert [r.ingame_name for r in rows] == ["A"]


def test_sellers_filter_by_rank():
    orders = [order("R0", 40, rank=0), order("R10", 45, rank=10), order("R10b", 48, rank=10)]
    assert [r.ingame_name for r in parse_sellers(orders, rank=10)] == ["R10", "R10b"]


def test_outliers_are_flagged_and_sorted_last_not_hidden():
    orders = [order(f"S{p}", p) for p in (28, 29, 30, 30, 31, 32)] + [order("Troll", 1), order("Gouge", 300)]
    rows = parse_sellers(orders)
    assert {r.ingame_name for r in rows if r.suspicious} == {"Troll", "Gouge"}
    assert [r.suspicious for r in rows][-2:] == [True, True]
    assert len(rows) == 8


class FakeClient:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, path):
        self.calls.append(path)
        return self.data


def test_seller_service_caches_briefly():
    client = FakeClient([order("A", 5)])
    service = SellerService(client, cache_s=60)
    service.sellers("x")
    service.sellers("x", include_online=False)
    assert client.calls == ["/orders/item/x"]
    expired = SellerService(client, cache_s=0)
    expired.sellers("x")
    expired.sellers("x")
    assert len(client.calls) == 3


# --- catalog ------------------------------------------------------------------------

def _entry(name, slug, tags, ducats=0, max_rank=None):
    e = {"slug": slug, "tags": tags, "ducats": ducats, "i18n": {"en": {"name": name}}}
    if max_rank is not None:
        e["maxRank"] = max_rank
    return e


def test_catalog_and_relic_rewards_come_from_one_fetch(tmp_path):
    data = [_entry("Primed Continuity", "primed_continuity", ["mod", "legendary"], max_rank=10),
            _entry("Burston Prime Blueprint", "burston_prime_blueprint", ["prime", "blueprint"], 15)]
    client = FakeClient(data)
    cache = tmp_path / "catalog.json"
    catalog = load_catalog(client, cache)
    rewards = load_items(client, cache)
    assert client.calls == ["/items"]
    assert {c.slug: c.max_rank for c in catalog} == {"burston_prime_blueprint": None, "primed_continuity": 10}
    assert [i.name for i in rewards] == ["Burston Prime Blueprint", "Forma Blueprint"]


def test_search_finds_partial_names():
    assert search_catalog("primed cont", CATALOG)[0].slug == "primed_continuity"
    assert search_catalog("burston bp", CATALOG)[0].slug.startswith("burston_prime")
    assert search_catalog("x", CATALOG) == []


# --- market window ----------------------------------------------------------------------

class FakeSellers:
    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def sellers(self, slug, rank=None, include_online=True):
        self.asked.append((slug, rank, include_online))
        return self.rows


def _wait(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_clicking_a_seller_copies_the_whisper(qapp):
    rows = [SellerOrder("Kosmo", "ingame", 45, 1, 10, 49, ""), SellerOrder("Ayla_09", "online", 48, 3, 10, 12, "")]
    sellers = FakeSellers(rows)
    window = MarketWindow(CATALOG, sellers)
    window.open_item("primed_continuity")
    _wait(qapp, lambda: window.table.rowCount() == 2)
    assert sellers.asked[-1] == ("primed_continuity", 10, True)  # defaults to max rank
    assert window.rank.isVisibleTo(window)
    window.copy_row(1)
    assert QApplication.clipboard().text() == \
        '/w Ayla_09 Hi! I want to buy: "Primed Continuity (rank 10)" for 48 platinum. (warframe.market)'
    assert "Ayla_09" in window.toast.text()
    window.shutdown()


def test_unranked_item_hides_rank_controls(qapp):
    window = MarketWindow(CATALOG, FakeSellers([SellerOrder("Kosmo", "ingame", 4, 1, None, 0, "")]))
    window.open_item("burston_prime_blueprint")
    _wait(qapp, lambda: window.table.rowCount() == 1)
    assert not window.rank.isVisibleTo(window)
    assert window.table.isColumnHidden(2)
    window.copy_row(0)
    assert QApplication.clipboard().text().endswith('"Burston Prime Blueprint" for 4 platinum. (warframe.market)')
    window.shutdown()


def test_typing_shows_suggestions_and_enter_opens_the_top_one(qapp):
    window = MarketWindow(CATALOG, FakeSellers([]))
    window.show()
    window._on_search("primed cont")
    assert window.suggestions.count() >= 1
    window._pick_first_suggestion()
    assert window.item.slug == "primed_continuity"
    _wait(qapp, lambda: "Nobody" in window.status.text())
    window.shutdown()


# --- hotkey ---------------------------------------------------------------------------

def test_hotkey_filter_reacts_only_to_its_own_hotkey():
    calls = []
    f = HotkeyFilter(lambda: calls.append(1))
    assert f.handle(WM_HOTKEY, HOTKEY_ID) is True
    assert f.handle(WM_HOTKEY, HOTKEY_ID + 1) is False
    assert f.handle(0x0100, HOTKEY_ID) is False  # WM_KEYDOWN
    assert calls == [1]
