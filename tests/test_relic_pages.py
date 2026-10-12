import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
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

    def cached_many(self, slugs):
        return {}


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
    section.show()
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
    section.show()
    section.show_page("unvaulted")
    settle(qapp, section)
    names = table_names(section.unvaulted)
    assert names == ["Axi Z1 Relic", "Lith Z2 Relic"]  # the vaulted one is not listed
    assert len(prices.asked) == len(set(prices.asked))  # each price fetched once


def test_squad_raises_the_value(qapp):
    section = RelicSection(ITEMS, FakePrices(), [OPEN])
    section.show()
    section.show_page("lookup")
    section.lookup.pick("Axi Z1 Relic")
    settle(qapp, section)
    solo = float(section.lookup.summary.item(0, 4).text())
    section.squad.setCurrentIndex(1)
    assert float(section.lookup.summary.item(0, 4).text()) > solo


# --- Task 4: sortable tables, vaulted split, chest icon, cached prices first ----------------------

from PyQt6.QtGui import QColor  # noqa: E402

COLS = range(1, 8)


def table_names(page):
    return [page.table.item(r, 1).text() for r in range(page.table.rowCount())]


def loaded_section(qapp, relics, prices=None):
    section = RelicSection(ITEMS, prices or FakePrices(), relics)
    section.show()  # pages load prices only while on screen
    section.show_page("unvaulted")
    section.show_page("vaulted")
    settle(qapp, section)
    section.unvaulted.render()  # the page not on screen catches up when it is shown again
    return section


def test_vaulted_and_unvaulted_pages_split(qapp):
    section = loaded_section(qapp, [OPEN, CHEAP, OLD])
    assert sorted(table_names(section.unvaulted)) == ["Axi Z1 Relic", "Lith Z2 Relic"]
    assert table_names(section.vaulted) == ["Meso Z3 Relic"]
    assert section.unvaulted.COLUMNS == ["", "Relic", "Era", "Intact plat", "Radiant plat", "Intact ducats",
                                         "Radiant ducats", "Best reward"]


def test_chest_colour_per_relic(qapp):
    section = loaded_section(qapp, [OPEN, OLD])
    for page, colour in ((section.vaulted, "#ff6b6b"), (section.unvaulted, "#5ccf8f")):
        img = page.table.item(0, 0).icon().pixmap(32, 32).toImage()
        assert QColor(img.pixel(16, 20)).name() == colour


def test_chest_icon_colours(qapp):
    from voidtrace.icons import chest_icon

    assert QColor(chest_icon(32, True).toImage().pixel(16, 20)).name() == "#ff6b6b"
    assert QColor(chest_icon(32, False).toImage().pixel(16, 20)).name() == "#5ccf8f"


def test_default_sort_is_radiant_plat_highest_first(qapp):
    section = loaded_section(qapp, [CHEAP, OPEN])
    assert table_names(section.unvaulted) == ["Axi Z1 Relic", "Lith Z2 Relic"]


THIRD = Relic("Neo Z4 Relic", False, tuple(Reward("Akstiletto Prime Barrel", r) for r in ["common"] * 3)
              + (Reward("Volt Prime Systems", "uncommon"), Reward("Fragor Prime Head", "uncommon"),
                 Reward("Braton Prime Stock", "rare")))


def test_sort_each_column_both_ways(qapp):
    section = loaded_section(qapp, [OPEN, CHEAP, THIRD])
    page = section.unvaulted
    for col in COLS:
        page.sort_by(col)
        first = table_names(page)
        page.sort_by(col)
        second = table_names(page)
        assert first != second, f"column {col} did not flip"
        keys = [page.sort_value(r, col) for r in page.rows]
        assert len(set(map(str, keys))) == len(keys) or col == 0


def test_unknown_values_sort_last(qapp):
    class SlowNikana(FakePrices):
        def get_price(self, slug):
            if slug == "nikana_prime_blueprint":
                return None
            return super().get_price(slug)

    section = loaded_section(qapp, [OPEN, CHEAP], SlowNikana())  # Axi Z1 has an unknown reward
    page = section.unvaulted
    page.sort_by(4)
    assert table_names(page)[-1] == "Axi Z1 Relic"
    page.sort_by(4)
    assert table_names(page)[-1] == "Axi Z1 Relic"


def test_cached_prices_show_before_fetch(qapp):
    gate = threading.Event()

    class Cached(FakePrices):
        def cached_many(self, slugs):
            return {slug: SimpleNamespace(price=PRICES[slug]) for slug in slugs}

        def get_price(self, slug):
            gate.wait(3)
            return super().get_price(slug)

    section = RelicSection(ITEMS, Cached(), [OPEN])
    section.show()
    section.show_page("unvaulted")
    qapp.processEvents()
    row = section.unvaulted.table
    assert "*" not in row.item(0, 4).text() and float(row.item(0, 4).text()) > 0
    assert "Loading prices" in section.unvaulted.status.text()
    gate.set()
    section.shutdown()


def test_double_click_opens_lookup(qapp):
    section = loaded_section(qapp, [OPEN, OLD])
    section.vaulted.open_relic.emit("Meso Z3 Relic")
    assert section.current_page() == "lookup"
    assert section.lookup.relic.name == "Meso Z3 Relic"


def test_prices_arriving_fast_redraw_at_most_once_per_interval(qapp):
    from voidtrace.ui.relics import RelicPrices

    data = RelicPrices(ITEMS, None)
    redraws = []
    data.changed.connect(lambda: redraws.append(1))
    for i in range(50):  # 50 prices in a burst
        data._on_price(f"slug{i}", 1.0)
    timer = data._changed_soon
    assert timer.isActive() and timer.interval() >= 1000  # one redraw later, not one per price
    timer.timeout.emit()
    assert redraws == [1]
