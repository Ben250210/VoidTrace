import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.items import Item
from voidtrace.ui.relics import RelicWindow
from voidtrace.relics import Relic, Reward

OPEN = Relic("Axi Z1 Relic", False, (
    Reward("Volt Prime Systems", "common"), Reward("Forma Blueprint", "common"), Reward("Braton Prime Stock", "common"),
    Reward("Akstiletto Prime Barrel", "uncommon"), Reward("Fragor Prime Head", "uncommon"),
    Reward("Nikana Prime Blueprint", "rare"),
))
CHEAP = Relic("Lith Z2 Relic", False, tuple(Reward("Braton Prime Stock", r) for r in ["common"] * 3 + ["uncommon"] * 2)
              + (Reward("Fragor Prime Head", "rare"),))
OLD = Relic("Meso Z3 Relic", True, OPEN.rewards)
ITEMS = [
    Item("Volt Prime Systems Blueprint", "volt_prime_systems_blueprint", 45),
    Item("Forma Blueprint", None, 0),
    Item("Braton Prime Stock", "braton_prime_stock", 15),
    Item("Akstiletto Prime Barrel", "akstiletto_prime_barrel", 45),
    Item("Fragor Prime Head", "fragor_prime_head", 45),
    Item("Nikana Prime Blueprint", "nikana_prime_blueprint", 100),
]
PRICES = {"volt_prime_systems_blueprint": 8, "braton_prime_stock": 2, "akstiletto_prime_barrel": 6,
          "fragor_prime_head": 5, "nikana_prime_blueprint": 60}


class FakePrices:
    def __init__(self):
        self.asked = []

    def get_price(self, slug):
        self.asked.append(slug)
        return SimpleNamespace(price=PRICES[slug])


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def settle(qapp, window):
    window.executor.shutdown(wait=True)
    qapp.processEvents()
    window.render()


def test_lookup_shows_values_and_rewards(qapp):
    prices = FakePrices()
    window = RelicWindow(ITEMS, prices, [OPEN, CHEAP, OLD])
    window.search.setText("axi z1")
    window._on_search("axi z1")
    settle(qapp, window)
    assert window.title.text().startswith("Axi Z1 Relic") and "farmable" in window.title.text()
    assert window.rewards.item(0, 0).text() == "Nikana Prime Blueprint"  # rare first
    assert window.rewards.item(0, 2).text() == "60"
    intact = float(window.summary.item(0, 1).text())
    radiant = float(window.summary.item(0, 4).text())
    assert intact == pytest.approx(round((8 + 0 + 2) * 0.2533 + (6 + 5) * 0.11 + 60 * 0.02, 1), abs=0.1)
    assert radiant > intact
    assert "*" not in window.summary.item(0, 4).text()
    assert "Forma" in window.note.text()
    window.shutdown()


def test_search_by_reward_and_vaulted_label(qapp):
    window = RelicWindow(ITEMS, FakePrices(), [OPEN, CHEAP, OLD])
    window.search.setText("nikana")
    window._on_search("nikana")
    assert [window.results.item(i).text() for i in range(window.results.count())] == ["Axi Z1 Relic", "Meso Z3 Relic"]
    window._pick("Meso Z3 Relic")
    assert "vaulted" in window.title.text()
    window.shutdown()


def test_best_unvaulted_sorted_by_radiant(qapp):
    prices = FakePrices()
    window = RelicWindow(ITEMS, prices, [CHEAP, OPEN, OLD])
    window.fetch_unvaulted()
    settle(qapp, window)
    names = [window.best.item(r, 0).text() for r in range(window.best.rowCount())]
    assert names == ["Axi Z1 Relic", "Lith Z2 Relic"]  # the vaulted one is not listed
    assert len(prices.asked) == len(set(prices.asked))  # each price fetched once


def test_squad_raises_the_value(qapp):
    window = RelicWindow(ITEMS, FakePrices(), [OPEN])
    window._pick("Axi Z1 Relic")
    settle(qapp, window)
    solo = float(window.summary.item(0, 4).text())
    window.squad.setCurrentIndex(1)
    assert float(window.summary.item(0, 4).text()) > solo
