import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from voidtrace import settings
from voidtrace.items import CatalogItem
from voidtrace.ui.main_window import MainWindow
from voidtrace.ui.market import MarketSection
from voidtrace.ui.relics import RelicSection
from voidtrace.ui.widgets import Section

LABELS = ["Buy", "Sell", "My orders", "Trades", "Alerts",
          "Find", "Price check", "Sell", "My rivens",
          "Unvaulted", "Vaulted", "Look up"]
KEYS = ["market/buy", "market/sell", "market/orders", "market/trades", "market/alerts",
        "rivens/find", "rivens/price", "rivens/sell", "rivens/mine",
        "relics/unvaulted", "relics/vaulted", "relics/lookup"]


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # never touch the user's real settings
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeAccount:
    signed_in = False
    ingame_name = None
    token = None

    def sign_out(self):
        self.signed_in = False


class FakeSellers:
    client = None

    def sellers(self, slug, rank=None, include_online=True):
        return []


class FakeRivens(Section):
    PAGES = {"find": "Find", "price": "Price check", "sell": "Sell", "mine": "My rivens"}

    def __init__(self, account):
        super().__init__()
        self.account = account
        self.refreshed = 0
        for key in self.PAGES:
            self.add_page(key, QLabel(key))

    def refresh_account(self):
        self.refreshed += 1


def make_window(load=lambda: "data", account=None):
    account = account or FakeAccount()
    market = MarketSection([CatalogItem("Volt Prime Set", "volt_prime_set", None, ("set",), "id")],
                           FakeSellers(), account=account)
    relics = RelicSection([], prices=None, relics=[])
    window = MainWindow(market, relics, load_riven_data=load, make_rivens=lambda data: FakeRivens(account))
    return window, account


def settle(qapp, condition, timeout=3.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def test_sidebar_lists_every_page_in_order(qapp):
    window, _ = make_window()
    assert window.sidebar_labels() == LABELS
    assert window.sidebar_keys() == KEYS


def test_real_riven_section_has_the_sidebar_pages():
    from voidtrace.ui.main_window import RIVEN_PAGES
    from voidtrace.ui.rivens import RivenSection

    assert RivenSection.PAGES == RIVEN_PAGES


def test_clicking_a_page_shows_it(qapp):
    window, _ = make_window()
    for key in KEYS:
        window.show_page(key)
        if key.startswith("rivens/"):
            assert settle(qapp, lambda: window.rivens() is not None)
            window.show_page(key)
        assert window.current_page() == key
        section = key.split("/")[0]
        assert window.stack.currentWidget() is window.section(section)
        assert window.section(section).current_page() == key.split("/")[1]


def test_last_page_is_remembered(qapp):
    window, _ = make_window()
    window.show_page("relics/vaulted")
    assert settings.get("last_page") == "relics/vaulted"
    again, _ = make_window()
    assert again.current_page() == "relics/vaulted"


def test_bad_last_page_falls_back(qapp):
    settings.put("last_page", "rivens/old")
    window, _ = make_window()
    assert window.current_page() == "market/buy"
    settings.put("last_page", 42)
    window, _ = make_window()
    assert window.current_page() == "market/buy"


def test_rivens_placeholder_until_loaded(qapp):
    gate = threading.Event()

    def slow():
        gate.wait(3)
        return "data"

    window, _ = make_window(load=slow)
    window.show_page("rivens/price")
    assert "Loading riven data" in window.riven_placeholder.text()
    assert window.stack.currentWidget() is window.riven_placeholder
    gate.set()
    assert settle(qapp, lambda: window.rivens() is not None)
    assert window.current_page() == "rivens/price"
    assert window.stack.currentWidget() is window.rivens()


def test_rivens_load_failure_is_shown(qapp):
    def broken():
        raise ConnectionError("offline")

    window, _ = make_window(load=broken)
    window.show_page("rivens/find")
    assert settle(qapp, lambda: "Could not load riven data" in window.riven_placeholder.text())
    window.show_page("market/buy")  # the rest still works
    assert window.current_page() == "market/buy"


def test_account_button_updates_both_sections(qapp):
    window, account = make_window()
    window.show_page("rivens/mine")
    assert settle(qapp, lambda: window.rivens() is not None)
    assert window.account_button.text() == "Sign in to Warframe.Market"
    before = window.rivens().refreshed
    account.signed_in, account.ingame_name = True, "Tenno"
    window.market.account_changed()  # what the sign-in dialog does when it succeeds
    assert window.account_button.text() == "Signed in as Tenno · Sign out"
    assert window.rivens().refreshed == before + 1
    window.account_button.click()  # signs out
    assert not account.signed_in
    assert window.account_button.text() == "Sign in to Warframe.Market"


def test_with_rivens_waits_for_the_section(qapp):
    gate = threading.Event()

    def slow():
        gate.wait(3)
        return "data"

    window, _ = make_window(load=slow)
    got = []
    window.with_rivens(got.append)
    assert got == []
    gate.set()
    assert settle(qapp, lambda: got == [window.rivens()])
    window.with_rivens(got.append)  # already loaded: right away
    assert len(got) == 2


def test_section_switching_itself_moves_the_sidebar(qapp):
    window, _ = make_window()
    window.show_page("relics/unvaulted")
    window.relics.show_relic("Axi A1 Relic")  # double-click in a table opens Look up
    assert window.current_page() == "relics/lookup"
    assert window.sidebar.currentItem().text(0) == "Look up"
