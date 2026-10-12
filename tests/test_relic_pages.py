import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.items import Item
from voidtrace.ui.relics import RelicSection
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


def settle(qapp, section):
    section.data.executor.shutdown(wait=True)
    qapp.processEvents()
    section.render()


def lookup(section, text):
    page = section.lookup
    page.search.setText(text)
    page._on_search(text)
    return page


def test_lookup_shows_values_and_rewards(qapp):
    section = RelicSection(ITEMS, FakePrices(), [OPEN, CHEAP, OLD])
    section.show_page("lookup")
    page = lookup(section, "axi z1")
    settle(qapp, section)
    assert page.title.text().startswith("Axi Z1 Relic") and "farmable" in page.title.text()
    assert page.rewards.item(0, 0).text() == "Nikana Prime Blueprint"  # rare first
    assert page.rewards.item(0, 2).text() == "60"
    intact = float(page.summary.item(0, 1).text())
    radiant = float(page.summary.item(0, 4).text())
    assert intact == pytest.approx(round((8 + 0 + 2) * 0.2533 + (6 + 5) * 0.11 + 60 * 0.02, 1), abs=0.1)
    assert radiant > intact
    assert "*" not in page.summary.item(0, 4).text()
    assert "Forma" in page.note.text()
    section.shutdown()


def test_search_by_reward_and_vaulted_label(qapp):
    section = RelicSection(ITEMS, FakePrices(), [OPEN, CHEAP, OLD])
    page = lookup(section, "nikana")
    assert [page.results.item(i).text() for i in range(page.results.count())] == ["Axi Z1 Relic", "Meso Z3 Relic"]
    page.pick("Meso Z3 Relic")
    assert "vaulted" in page.title.text()
    section.shutdown()


def test_unvaulted_page_sorted_by_radiant(qapp):
    prices = FakePrices()
    section = RelicSection(ITEMS, prices, [CHEAP, OPEN, OLD])
    section.show_page("unvaulted")
    settle(qapp, section)
    table = section.unvaulted.table
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert names == ["Axi Z1 Relic", "Lith Z2 Relic"]  # the vaulted one is not listed
    assert len(prices.asked) == len(set(prices.asked))  # each price fetched once


def test_squad_raises_the_value(qapp):
    section = RelicSection(ITEMS, FakePrices(), [OPEN])
    section.show_page("lookup")
    section.lookup.pick("Axi Z1 Relic")
    settle(qapp, section)
    solo = float(section.lookup.summary.item(0, 4).text())
    section.squad.setCurrentIndex(1)
    assert float(section.lookup.summary.item(0, 4).text()) > solo
