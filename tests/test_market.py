import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.hotkey import HOTKEY_ID, WM_HOTKEY, HotkeyFilter
from voidtrace.items import CatalogItem, load_catalog, load_items
from voidtrace.search import search_catalog
from voidtrace.ui.market import MarketSection
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
        '/w Kosmo Hi! I want to buy: "Burston Prime Blueprint" for 5 platinum. (warframe.market through VoidTrace)'


def test_whisper_for_ranked_mod_includes_rank():
    s = SellerOrder("Ayla_09", "online", 45, 3, 10, 49, "")
    assert whisper_text(s, "Primed Continuity", ranked=True) == \
        '/w Ayla_09 Hi! I want to buy: "Primed Continuity (rank 10)" for 45 platinum. (warframe.market through VoidTrace)'


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


SEARCH = CATALOG + [
    CatalogItem("Arcane Energize", "arcane_energize", 5, ("arcane",)),
    CatalogItem("Arcane Avenger", "arcane_avenger", 5, ("arcane",)),
    CatalogItem("Acceltra Prime Blueprint", "acceltra_prime_blueprint", None, ("prime",)),
    CatalogItem("Serration", "serration", 10, ("mod",)),
    CatalogItem("Spectral Serration", "spectral_serration", 3, ("mod",)),
    CatalogItem("Archon Vitality", "archon_vitality", 10, ("mod",)),
    CatalogItem("Vitality", "vitality", 10, ("mod",)),
]


@pytest.mark.parametrize("query,first", [
    ("primed cont", "Primed Continuity"),          # starts with the query
    ("arcane ener", "Arcane Energize"),            # every word starts a name word
    ("burston prime bp", "Burston Prime Blueprint"),  # "bp" alias; WRatio picked Acceltra here
    ("serration", "Serration"),                    # shorter name wins ties
    ("serat", "Serration"),                        # typo
    ("vitalty", "Vitality"),                       # typo, shorter wins
    ("primd contnuity", "Primed Continuity"),      # two typos
])
def test_search_ranks_like_a_shop_search(query, first):
    assert search_catalog(query, SEARCH)[0].name == first


def test_search_does_not_pad_exact_hits_with_fuzzy_junk():
    assert [i.name for i in search_catalog("primed continuity", SEARCH)] == ["Primed Continuity"]


def test_search_rejects_nonsense():
    assert search_catalog("xyzzy", SEARCH) == []
    assert search_catalog("x", SEARCH) == []


def test_outliers_are_judged_within_each_rank():
    # Rank 0 sells ~45p, rank 10 ~100p: neither group is an outlier of the other.
    orders = [order(f"r0_{p}", p, rank=0) for p in (44, 45, 45, 46, 48)] +              [order(f"r10_{p}", p, rank=10) for p in (98, 100, 100, 105, 110)] + [order("Troll", 5, rank=10)]
    rows = parse_sellers(orders)
    assert {r.ingame_name for r in rows if r.suspicious} == {"Troll"}


# --- market window ----------------------------------------------------------------------

class FakeTopClient:
    """Answers the top-5 request the Post order tab makes when an item opens."""

    def get(self, path):
        return {"sell": [], "buy": []}


class FakeSellers:
    def __init__(self, rows):
        self.rows, self.asked = rows, []
        self.client = FakeTopClient()

    def sellers(self, slug, rank=None, include_online=True):
        self.asked.append((slug, rank, include_online))
        return self.rows


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    from test_account import MemoryKeyring
    import voidtrace.account as account_module

    monkeypatch.setattr(account_module.TokenStore, "__init__",
                        lambda self, backend=None: setattr(self, "_kr", backend or MemoryKeyring()))


def _wait(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_clicking_a_seller_copies_the_whisper(qapp):
    rows = [SellerOrder("Kosmo", "ingame", 45, 1, 10, 49, ""), SellerOrder("Ayla_09", "online", 48, 3, 10, 12, "")]
    sellers = FakeSellers(rows)
    window = MarketSection(CATALOG, sellers)
    window.open_item("primed_continuity")
    _wait(qapp, lambda: window.table.rowCount() == 2)
    assert sellers.asked[-1] == ("primed_continuity", None, True)  # any rank, like warframe.market
    assert window.rank.isVisibleTo(window)
    assert window.rank.itemText(0) == "Any rank (2)"
    assert window.rank.itemText(11) == "Rank 10 (2)"
    # Labels must fit: the box and its list grow instead of eliding to "Ran...(0)".
    metrics = window.rank.fontMetrics()
    assert window.rank.sizeHint().width() > metrics.horizontalAdvance("Any rank (2)")
    assert window.rank.view().minimumWidth() >= metrics.horizontalAdvance("Rank 10 (2)")
    assert "max rank 10" in window.title.text()
    window.copy_row(1)
    assert QApplication.clipboard().text() == \
        '/w Ayla_09 Hi! I want to buy: "Primed Continuity (rank 10)" for 48 platinum. (warframe.market through VoidTrace)'
    assert "Ayla_09" in window.toast.text()
    window.shutdown()


def test_unranked_item_hides_rank_controls(qapp):
    window = MarketSection(CATALOG, FakeSellers([SellerOrder("Kosmo", "ingame", 4, 1, None, 0, "")]))
    window.open_item("burston_prime_blueprint")
    _wait(qapp, lambda: window.table.rowCount() == 1)
    assert not window.rank.isVisibleTo(window)
    assert window.table.isColumnHidden(2)
    window.copy_row(0)
    assert QApplication.clipboard().text().endswith('"Burston Prime Blueprint" for 4 platinum. (warframe.market through VoidTrace)')
    window.shutdown()


def test_typing_shows_suggestions_and_enter_opens_the_top_one(qapp):
    window = MarketSection(CATALOG, FakeSellers([]))
    window.show()
    window._on_search("primed cont")
    assert window.suggestions.count() >= 1
    assert window.suggestions.item(0).text() == "Primed Continuity"  # no "(max rank 10)" in suggestions
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


# --- posting orders from the window ------------------------------------------------------

def _signed_in_window(qapp, top):
    from test_account import FakeHTTP, MemoryKeyring
    from voidtrace.account import Account, TokenStore

    http = FakeHTTP()
    http.top = top
    acc = Account(http, TokenStore(MemoryKeyring()), device="test")
    acc.sign_in("me", "hunter2")
    sellers = FakeSellers([])
    sellers.client = http
    catalog = CATALOG + [CatalogItem("Necramech Fury", "necramech_fury", 3, ("mod",), "5f4ed3f9d5c36d0086f55b0a")]
    window = MarketSection(catalog, sellers, account=acc)
    return window, http


def _top_order(name, plat, status="ingame"):
    return {"platinum": plat, "quantity": 1, "rank": 3, "user": {"ingameName": name, "reputation": 5, "status": status}}


def test_post_order_tab_shows_top_five_and_posts_undercut(qapp):
    top = {"sell": [_top_order("August159", 9), _top_order("attentionTOT", 10)], "buy": [_top_order("buyer", 6)]}
    window, http = _signed_in_window(qapp, top)
    window.open_item("necramech_fury")
    panel = window.post_panel
    _wait(qapp, lambda: panel.sell_table.rowCount() == 2)
    assert "maxed rank" in panel.sell_title.text()
    assert http.calls[-1][1] == "/orders/item/necramech_fury/top?rank=3"   # same rank as the order
    assert panel.price() == 9 and "9p" in panel.price_label.text()          # match lowest
    panel.mode_offset.setChecked(True)
    panel.offset.setValue(-1)
    assert panel.price() == 8
    panel.post_order()
    _wait(qapp, lambda: "Posted" in panel.result.text())
    method, url, headers, body = next(c for c in http.calls if c[1] == "/order")
    assert body == {"itemId": "5f4ed3f9d5c36d0086f55b0a", "type": "sell", "platinum": 8, "quantity": 1,
                    "visible": True, "rank": 3}
    window.shutdown()


def test_buy_side_matches_highest_buyer(qapp):
    top = {"sell": [_top_order("s", 9)], "buy": [_top_order("b1", 5), _top_order("b2", 7)]}
    window, http = _signed_in_window(qapp, top)
    window.open_item("necramech_fury")
    panel = window.post_panel
    _wait(qapp, lambda: panel.buy_table.rowCount() == 2)
    panel.side.setCurrentIndex(1)
    assert panel.price() == 7
    window.shutdown()


def test_signed_out_post_button_asks_to_sign_in(qapp):
    window = MarketSection(CATALOG, FakeSellers([]))
    assert not window.account.signed_in
    assert window.post_panel.post.text() == "Sign in to post"
    window.shutdown()


def test_my_orders_lists_and_deletes(qapp):
    window, http = _signed_in_window(qapp, {"sell": [], "buy": []})
    window.account.create_order("5f4ed3f9d5c36d0086f55b0a", "sell", 12, rank=3)
    panel = window.orders_panel
    panel.refresh()
    _wait(qapp, lambda: panel.table.rowCount() == 1)
    assert panel.table.item(0, panel.ITEM).text() == "Necramech Fury"
    panel.delete(panel.orders[0])
    _wait(qapp, lambda: http.orders == [] and panel.table.rowCount() == 0)
    window.shutdown()


def test_offset_plus_and_minus_buttons_both_work(qapp):
    # Live test: the styled spin box's up arrow never responded; the steppers use real buttons.
    window, http = _signed_in_window(qapp, {"sell": [_top_order("a", 20)], "buy": []})
    window.open_item("necramech_fury")
    panel = window.post_panel
    _wait(qapp, lambda: panel.sell_table.rowCount() == 1)
    panel.mode_offset.setChecked(True)
    panel.offset.setValue(-3)
    panel.offset.plus.click()
    panel.offset.plus.click()
    assert panel.offset.value() == -1 and panel.price() == 19
    panel.offset.minus.click()
    assert panel.offset.value() == -2 and "18p" in panel.price_label.text()
    panel.quantity.plus.click()
    assert panel.quantity.value() == 2
    window.shutdown()


def test_post_rank_box_fits_maxed_rank(qapp):
    window, _ = _signed_in_window(qapp, {"sell": [], "buy": []})
    window.open_item("necramech_fury")
    box = window.post_panel.rank
    assert box.currentText() == "Maxed rank"
    width = box.fontMetrics().horizontalAdvance("Maxed rank")
    assert box.sizeHint().width() > width and box.view().minimumWidth() >= width
    window.shutdown()


def test_my_orders_eye_filters_and_toggle(qapp):
    window, http = _signed_in_window(qapp, {"sell": [], "buy": []})
    acc = window.account
    acc.create_order("5f4ed3f9d5c36d0086f55b0a", "sell", 12, rank=3)
    acc.create_order("5f4ed3f9d5c36d0086f55b0a", "buy", 5, rank=3, visible=False)
    panel = window.orders_panel
    panel.refresh()
    _wait(qapp, lambda: panel.table.rowCount() == 2)
    assert "(hidden)" not in panel.table.item(1, panel.ITEM).text()      # eye icon instead of text
    assert "Click to show" in panel.table.cellWidget(1, panel.EYE).toolTip()
    assert panel.status.text() == "2 orders, 1 hidden"

    panel.state_filter.setCurrentIndex(2)                                  # Hidden only
    assert [o.type for o in panel.shown] == ["buy"] and "Showing 1 of 2" in panel.status.text()
    panel.state_filter.setCurrentIndex(0)
    panel.type_filter.setCurrentIndex(1)                                   # Sell only
    assert [o.type for o in panel.shown] == ["sell"]
    panel.type_filter.setCurrentIndex(0)

    panel.table.cellWidget(0, panel.EYE).click()                           # hide the sell order
    _wait(qapp, lambda: http.orders[0]["visible"] is False and not panel.orders[0].visible)
    window.shutdown()


def test_order_buttons_fit_their_columns(qapp):
    window, http = _signed_in_window(qapp, {"sell": [], "buy": []})
    window.account.create_order("5f4ed3f9d5c36d0086f55b0a", "sell", 12, rank=3)
    panel = window.orders_panel
    panel.refresh()
    _wait(qapp, lambda: panel.table.rowCount() == 1)
    for col in (panel.MATCH, panel.DELETE):
        button = panel.table.cellWidget(0, col)
        assert panel.table.columnWidth(col) >= button.sizeHint().width()
        assert panel.table.rowHeight(0) >= button.sizeHint().height()
    window.shutdown()


def test_my_orders_sorting(qapp):
    window, http = _signed_in_window(qapp, {"sell": [], "buy": []})
    acc = window.account
    item = "5f4ed3f9d5c36d0086f55b0a"
    acc.create_order(item, "sell", 12, rank=3, visible=False)
    acc.create_order(item, "sell", 30, rank=1)
    acc.create_order(item, "sell", 5, rank=2, visible=False)
    acc.create_order(item, "sell", 20, rank=0)
    panel = window.orders_panel
    panel.refresh()
    _wait(qapp, lambda: panel.table.rowCount() == 4)
    header = lambda col: panel.table.horizontalHeaderItem(col).text()  # noqa: E731

    # Default: visible orders first, then hidden.
    assert [o.visible for o in panel.shown] == [True, True, False, False]
    assert header(panel.EYE).endswith("\u25b2")

    panel.sort_by(panel.PRICE)                   # first click: highest price first
    assert [o.platinum for o in panel.shown] == [30, 20, 12, 5]
    assert header(panel.PRICE) == "Price \u25bc" and header(panel.EYE) == ""
    panel.sort_by(panel.PRICE)                   # second click: lowest first
    assert [o.platinum for o in panel.shown] == [5, 12, 20, 30]
    assert header(panel.PRICE) == "Price \u25b2"

    panel.sort_by(panel.RANK)
    assert [o.rank for o in panel.shown] == [3, 2, 1, 0]
    panel.sort_by(panel.EYE)                     # back to state: visible first
    assert [o.visible for o in panel.shown] == [True, True, False, False]
    panel.sort_by(panel.EYE)                     # flipped: hidden first
    assert [o.visible for o in panel.shown] == [False, False, True, True]

    panel.sort_by(panel.MATCH)                   # button columns don't sort
    assert [o.visible for o in panel.shown] == [False, False, True, True]
    window.shutdown()


def test_mouse_wheel_does_not_change_the_rank_picker(qapp):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent

    window = MarketSection(CATALOG, FakeSellers([SellerOrder("Kosmo", "ingame", 45, 1, 10, 49, "")]))
    window.open_item("primed_continuity")
    window.rank.setCurrentIndex(3)
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(window.rank, wheel)
    assert window.rank.currentIndex() == 3
    window.shutdown()
