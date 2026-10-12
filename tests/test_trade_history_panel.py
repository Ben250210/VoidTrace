import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.ui.trades import TradeHistoryPanel, items_text
from voidtrace.trades import Trade, TradeHistory, TradeItem, TradeRecord

NOW = datetime(2026, 10, 12, 18, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def history(tmp_path):
    h = TradeHistory(tmp_path / "trades.jsonl")
    h.add(Trade("Old", (TradeItem("Forma Blueprint", 3),), (TradeItem("Platinum", 30),)), datetime(2026, 9, 1, 10))
    h.add(Trade("Buyer", (TradeItem("Hollow Point", 1, 5),), (TradeItem("Platinum", 10),)), datetime(2026, 10, 12, 9))
    h.add(Trade("Seller", (TradeItem("Platinum", 25),), (TradeItem("Volt Prime Systems Blueprint", 1),)),
          datetime(2026, 10, 12, 10))
    return h


def test_items_text():
    assert items_text((TradeItem("Hollow Point", 1, 5), TradeItem("Platinum", 10))) == "Hollow Point (R5)"
    assert items_text((TradeItem("Forma Blueprint", 3),)) == "Forma Blueprint ×3"
    assert items_text((TradeItem("Platinum", 10),)) == "—"


def test_panel_rows_newest_first_and_totals(qapp, history):
    panel = TradeHistoryPanel(history, now=lambda: NOW)
    panel.refresh()
    t = panel.table
    assert t.rowCount() == 3
    assert [t.item(r, panel.PARTNER).text() for r in range(3)] == ["Seller", "Buyer", "Old"]
    assert t.item(0, panel.SIDE).text() == "Bought" and t.item(0, panel.PLAT).text() == "-25"
    assert t.item(1, panel.SIDE).text() == "Sold" and t.item(1, panel.PLAT).text() == "+10"
    assert t.item(0, panel.WHEN).text() == "10:00"
    assert t.item(2, panel.WHEN).text() == "01 Sep 10:00"
    assert panel.today.text().startswith("Today: -15p net (10 in, 25 out, 2 trades)")
    assert panel.all_time.text().startswith("All time: +15p net")


def test_new_trade_shows_without_refresh(qapp, history):
    panel = TradeHistoryPanel(history, now=lambda: NOW)
    panel.refresh()
    record = history.add(Trade("New", (TradeItem("Hollow Point", 1, 5),), (TradeItem("Platinum", 12),)),
                         datetime(2026, 10, 12, 17))
    panel.add(record)
    assert panel.table.rowCount() == 4 and panel.table.item(0, panel.PARTNER).text() == "New"


def test_first_trade_before_the_tab_was_opened_loads_the_file(qapp, history):
    panel = TradeHistoryPanel(history, now=lambda: NOW)
    record = history.add(Trade("New", (), (TradeItem("Platinum", 1),)), datetime(2026, 10, 12, 17))
    panel.add(record)
    assert panel.table.rowCount() == 4
