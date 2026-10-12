"""Regression tests for the findings of the one-window branch review."""
import ast
import importlib
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from voidtrace import settings
from voidtrace.items import CatalogItem
from voidtrace.ui.widgets import Section

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- 3: the dev tools only import names that exist --------------------------------------------------

@pytest.mark.parametrize("script", sorted((ROOT / "tools").rglob("*.py")), ids=lambda p: p.name)
def test_tools_import_only_existing_voidtrace_names(script):
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("voidtrace"):
            module = importlib.import_module(node.module)
            for alias in node.names:
                if not hasattr(module, alias.name):
                    importlib.import_module(f"{node.module}.{alias.name}")  # a submodule, or it raises


# --- 1 + 2: relic prices: one cache read, nothing fetched while hidden --------------------------------

from tests.test_relic_pages import ITEMS, OLD, OPEN, FakePrices  # noqa: E402
from voidtrace.ui.relics import RelicSection  # noqa: E402


class CountingPrices(FakePrices):
    def __init__(self):
        super().__init__()
        self.cache_reads = 0

    def cached(self, slug):
        raise AssertionError("cached() once per slug: use cached_many()")

    def cached_many(self, slugs):
        self.cache_reads += 1
        return {}


def test_relic_table_reads_the_price_cache_once(qapp):
    prices = CountingPrices()
    section = RelicSection(ITEMS, prices, [OPEN, OLD])
    section.show()
    section.show_page("unvaulted")
    assert prices.cache_reads == 1
    section.shutdown()


def test_price_service_cached_many_reads_the_file_once(tmp_path, monkeypatch):
    import time

    from voidtrace.price_cleaner import JsonPriceCache, PriceService, PriceStats

    cache = JsonPriceCache(tmp_path / "p.json")
    for slug in ("a", "b"):
        cache.put(PriceStats(slug, 5.0, "listings", 0, 5.0, 5.0, 5.0, 3, 0, "ingame", time.time() - 9999))
    reads = []
    real = cache._load
    monkeypatch.setattr(cache, "_load", lambda: reads.append(1) or real())
    got = PriceService(object(), cache).cached_many(["a", "b", "c"])
    assert {k: v.price for k, v in got.items()} == {"a": 5.0, "b": 5.0}
    assert len(reads) == 1


def test_hidden_relic_pages_fetch_nothing(qapp):
    prices = FakePrices()
    section = RelicSection(ITEMS, prices, [OPEN, OLD])  # the Look up page picks a relic at start
    section.show_page("vaulted")
    assert section.data.requested == set() and prices.asked == []
    section.show()
    section.show_page("vaulted")
    assert section.data.requested
    section.shutdown()


# --- 2 + 4: the main window's entry points ----------------------------------------------------------

class FakeAccount:
    signed_in = False
    ingame_name = None


class FakeSellers:
    client = None

    def sellers(self, slug, rank=None, include_online=True):
        return []


class FakeRivens(Section):
    PAGES = {"find": "Find", "price": "Price check", "sell": "Sell", "mine": "My rivens"}

    def __init__(self):
        super().__init__()
        self.calls = []
        for key in self.PAGES:
            self.add_page(key, QLabel(key))

        class Panel:
            def run_check(inner):
                self.calls.append("run_check")

        self.price_panel = Panel()

    def load_draft(self, draft):
        self.calls.append(("draft", draft))

    def read_from_frame(self, frame):
        self.calls.append(("frame", frame))


def make_window(prices=None, last=None):
    from voidtrace.ui.main_window import MainWindow
    from voidtrace.ui.market import MarketSection

    if last:
        settings.put("last_page", last)
    item = CatalogItem("Volt Prime Set", "volt_prime_set", None, ("set",), "id")
    market = MarketSection([item], FakeSellers(), account=FakeAccount())
    relics = RelicSection(ITEMS, prices or FakePrices(), [OPEN, OLD])
    rivens = FakeRivens()
    return MainWindow(market, relics, load_riven_data=lambda: "d", make_rivens=lambda d: rivens), rivens


def wait(qapp, cond, timeout=3.0):
    import time

    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


def test_hidden_window_on_a_relic_page_fetches_nothing_until_shown(qapp):
    prices = FakePrices()
    window, _ = make_window(prices, last="relics/vaulted")
    assert window.relics.data.requested == set()
    window.open_page()
    assert wait(qapp, lambda: bool(prices.asked))
    window.shutdown()


def test_riven_hotkey_reads_into_price_check(qapp):
    window, rivens = make_window()
    window.read_riven("FRAME")
    assert wait(qapp, lambda: ("frame", "FRAME") in rivens.calls)
    assert window.current_page() == "rivens/price" and window.isVisible()
    window.hide()


def test_riven_popup_opens_price_check_with_the_draft(qapp):
    window, rivens = make_window()
    window.open_riven_draft("DRAFT")
    assert wait(qapp, lambda: rivens.calls[-2:] == [("draft", "DRAFT"), "run_check"])
    assert window.current_page() == "rivens/price"
    window.hide()


def test_reward_card_opens_its_item_on_buy(qapp):
    window, _ = make_window(last="relics/vaulted")
    window.open_item("volt_prime_set")
    assert window.current_page() == "market/buy"
    assert window.market.item.slug == "volt_prime_set"
    window.hide()


def test_open_page_without_a_key_reopens_the_last_page(qapp):
    window, _ = make_window(last="market/trades")
    window.open_page()
    assert window.current_page() == "market/trades" and window.isVisible()
    window.hide()


# --- 6: the status line only mentions missing prices when some are missing ---------------------------

def test_relic_status_line_without_missing_prices(qapp):
    section = RelicSection(ITEMS, FakePrices(), [OPEN])
    section.show()
    section.show_page("unvaulted")
    section.data.executor.shutdown(wait=True)
    qapp.processEvents()
    section.unvaulted.render()
    assert section.unvaulted.status.text() == "1 relics · double-click one for its rewards"
    section.shutdown()


# --- 8: sidebar: arrow keys open pages, headers do not collapse ---------------------------------------

def test_sidebar_arrow_keys_open_the_page(qapp):
    window, _ = make_window(last="market/buy")
    window.sidebar.setFocus()
    QTest.keyClick(window.sidebar, Qt.Key.Key_Down)
    assert window.current_page() == "market/sell"
    window.hide()


def test_sidebar_headers_do_not_collapse(qapp):
    window, _ = make_window()
    assert not window.sidebar.itemsExpandable()


# --- 10: searching from another Market page shows the item ---------------------------------------------

def test_search_from_trades_page_shows_buy(qapp):
    window, _ = make_window(last="market/trades")
    window.market.open_item("volt_prime_set")
    assert window.market.current_page() == "buy"
    assert window.current_page() == "market/buy"


def test_window_opens_maximized(qapp):
    window, _ = make_window()
    window.open_page("market/buy")
    assert window.windowState() & Qt.WindowState.WindowMaximized
    window.hide()
