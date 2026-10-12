import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from voidtrace.account import MyOrder
from voidtrace.items import CatalogItem
from voidtrace.log_watcher import LogWatcher
from voidtrace.trades import (OrderChange, Totals, Trade, TradeHistory, TradeItem, TradeReader, TradeRecord,
                              apply_changes, item_finder, parse_item, plan_changes, summary, totals, undo)

# A real trade from EE.log (2026-10-11), the other player's name replaced.
REAL_TRADE = """\
342.257 Script [Info]: Trade.lua: Missing icon data!
342.423 Sys [Info]: Created /Lotus/Interface/Dialog.swf
342.423 Script [Info]: Dialog.lua: Dialog::CreateOkCancel(description=Are you sure you want to accept this trade? You are offering:
Hollow Point (RARE RANK 5)

and will receive from TraderJoe the following:
Platinum x 10, title= leftItem=/Menu/Confirm_Item_Ok, rightItem=/Menu/Confirm_Item_Cancel)
345.465 Script [Info]: Dialog.lua: SendResult_MENU_SELECT()
345.465 Script [Info]: Dialog.lua: Dialog::SendResult(4)
346.829 Sys [Info]: Spot-loading /Lotus/Scripts/Challenges/VehicleKill.lua during batch loading!
347.379 Sys [Info]: Created /Lotus/Interface/Dialog.swf
347.379 Script [Info]: Dialog.lua: Dialog::CreateOk(description=The trade was successful!, title= leftItem=/Menu/Confirm_Item_Ok)
347.796 Script [Info]: Dialog.lua: SendResult_MENU_SELECT()
""".splitlines()

CATALOG = [
    CatalogItem("Hollow Point", "hollow_point", 5, ("mod",), "id-hp"),
    CatalogItem("Volt Prime Systems Blueprint", "volt_prime_systems", None, ("prime",), "id-volt"),
    CatalogItem("Forma Blueprint", "forma_blueprint", None, (), "id-forma"),
]
FIND = item_finder(CATALOG)


def read(lines):
    reader = TradeReader()
    return [t for t in map(reader.feed, lines) if t is not None]


def order(id, item_id, type="sell", quantity=1, rank=None, platinum=10, visible=True):
    return MyOrder(id, item_id, type, platinum, quantity, rank, visible)


# --- reading trades from the log ---------------------------------------------------------------

def test_reads_the_real_trade():
    assert read(REAL_TRADE) == [Trade("TraderJoe", (TradeItem("Hollow Point", 1, 5),),
                                      (TradeItem("Platinum", 10),))]
    trade = read(REAL_TRADE)[0]
    assert (trade.plat_got, trade.plat_given) == (10, 0)


def test_cancelled_trade_is_ignored():
    cancelled = [line for line in REAL_TRADE if "successful" not in line]
    assert read(cancelled) == []


def test_success_long_after_is_not_this_trade():
    late = REAL_TRADE[:7] + ["600.000 Script [Info]: Dialog.lua: Dialog::CreateOk(description=The trade was successful!, title=)"]
    assert read(late) == []


def test_several_items_counts_and_ranks():
    lines = [
        "10.0 Script [Info]: Dialog.lua: Dialog::CreateOkCancel(description=Are you sure you want to accept this trade? You are offering:",
        "Volt Prime Systems Blueprint x 2",
        "Hollow Point (RARE RANK 3)",
        "",
        "and will receive from Some_Body-1 the following:",
        "Platinum x 45, title= leftItem=/Menu/Confirm_Item_Ok, rightItem=/Menu/Confirm_Item_Cancel)",
        "12.0 Script [Info]: Dialog.lua: Dialog::CreateOk(description=The trade was successful!, title=)",
    ]
    (trade,) = read(lines)
    assert trade.partner == "Some_Body-1"
    assert trade.gave == (TradeItem("Volt Prime Systems Blueprint", 2), TradeItem("Hollow Point", 1, 3))
    assert trade.plat_got == 45


def test_cut_off_dialog_is_dropped():
    lines = REAL_TRADE[:4] + ["343.0 Script [Info]: something else"] + REAL_TRADE[7:]
    assert read(lines) == []


def test_parse_item():
    assert parse_item("Forma Blueprint x3") == TradeItem("Forma Blueprint", 3)
    assert parse_item("Arcane Energize (LEGENDARY RANK 0)") == TradeItem("Arcane Energize", 1, 0)
    assert parse_item("   ") is None


def test_log_watcher_passes_every_line():
    seen = []

    class Source:
        def poll(self):
            return []

        def close(self):
            pass

    watcher = LogWatcher(None, on_event=lambda e: None, source=Source(), on_line=seen.append)
    watcher.handle_batch(REAL_TRADE)
    assert seen == REAL_TRADE


# --- matching orders -----------------------------------------------------------------------------

def test_sold_item_lowers_the_sell_order_of_that_rank():
    trade = Trade("x", (TradeItem("Hollow Point", 1, 5),), (TradeItem("Platinum", 10),))
    orders = [order("buy", "id-hp", "buy", 3), order("r0", "id-hp", rank=0, quantity=2),
              order("r5", "id-hp", rank=5, quantity=3)]
    (change,) = plan_changes(trade, orders, FIND)
    assert change.order.id == "r5"
    assert (change.new_quantity, change.deletes) == (2, False)


def test_last_one_sold_deletes_the_order():
    trade = Trade("x", (TradeItem("Volt Prime Systems Blueprint", 2),), ())
    (change,) = plan_changes(trade, [order("v", "id-volt", quantity=2)], FIND)
    assert change.deletes


def test_trade_more_than_the_order_holds_deletes_it():
    trade = Trade("x", (TradeItem("Volt Prime Systems Blueprint", 5),), ())
    (change,) = plan_changes(trade, [order("v", "id-volt", quantity=2)], FIND)
    assert change.traded == 2 and change.deletes


def test_received_items_lower_buy_orders():
    trade = Trade("x", (TradeItem("Platinum", 20),), (TradeItem("Forma Blueprint", 1),))
    orders = [order("s", "id-forma", "sell", 4), order("b", "id-forma", "buy", 4)]
    (change,) = plan_changes(trade, orders, FIND)
    assert change.order.id == "b" and change.new_quantity == 3


def test_no_matching_order_or_unknown_item_changes_nothing():
    trade = Trade("x", (TradeItem("Some Riven Name", 1, 8), TradeItem("Hollow Point", 1, 5)), ())
    assert plan_changes(trade, [order("other", "id-forma")], FIND) == []


def test_unranked_order_matches_a_ranked_item():
    trade = Trade("x", (TradeItem("Hollow Point", 1, 5),), ())
    (change,) = plan_changes(trade, [order("any", "id-hp", rank=None)], FIND)
    assert change.order.id == "any"


def test_blueprint_suffix_is_optional():
    assert FIND("Volt Prime Systems").id == "id-volt"


# --- applying and undoing ---------------------------------------------------------------------------

class FakeAccount:
    signed_in = True

    def __init__(self, orders=(), fail=()):
        self.orders = list(orders)
        self.calls = []
        self.fail = set(fail)

    def my_orders(self):
        return self.orders

    def update_order(self, order_id, **fields):
        if order_id in self.fail:
            raise RuntimeError("refused")
        self.calls.append(("update", order_id, fields))

    def delete_order(self, order_id):
        self.calls.append(("delete", order_id))

    def create_order(self, *args):
        self.calls.append(("create",) + args)


def test_apply_then_undo():
    lower = OrderChange(order("a", "id-hp", quantity=3, rank=5), "Hollow Point", 1)
    remove = OrderChange(order("b", "id-volt", quantity=1, platinum=25, visible=False), "Volt", 1)
    account = FakeAccount()
    assert apply_changes(account, [lower, remove]) == [lower, remove]
    assert account.calls == [("update", "a", {"quantity": 2}), ("delete", "b")]
    account.calls.clear()
    undo(account, [lower, remove])
    assert account.calls == [("update", "a", {"quantity": 3}), ("create", "id-volt", "sell", 25, 1, None, False)]


def test_a_refused_change_is_left_out():
    change = OrderChange(order("a", "id-hp", quantity=3), "Hollow Point", 1)
    assert apply_changes(FakeAccount(fail={"a"}), [change]) == []


def test_summary_text():
    trade = Trade("TraderJoe", (TradeItem("Hollow Point", 1, 5),), (TradeItem("Platinum", 10),))
    change = OrderChange(order("a", "id-hp", quantity=3), "Hollow Point", 1)
    assert summary(trade, [change]) == "Sold Hollow Point ×1 to TraderJoe for 10p\nHollow Point ×1: order 3 → 2"


# --- the tracker and its toast ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_tracker_updates_orders_and_shows_undo(qapp, tmp_path):
    from voidtrace.trade_toast import TradeToast, TradeTracker

    account = FakeAccount([order("r5", "id-hp", rank=5, quantity=3)])
    toast = TradeToast(QRect(0, 0, 1920, 1080))
    refreshed = []
    tracker = TradeTracker(account, CATALOG, toast, is_enabled=lambda: True,
                           on_orders_changed=lambda: refreshed.append(1), history=TradeHistory(tmp_path / "t.jsonl"))
    tracker.executor.submit = lambda fn, *a: fn(*a)  # run right away in the test
    for line in REAL_TRADE:
        tracker.feed(line)
    qapp.processEvents()
    assert account.calls == [("update", "r5", {"quantity": 2})]
    assert toast.isVisible() and "order 3 → 2" in toast.text.text()
    assert refreshed
    toast.undo_button.click()
    qapp.processEvents()
    assert account.calls[-1] == ("update", "r5", {"quantity": 3})
    assert "put back" in toast.text.text()
    toast.hide()
    tracker.shutdown()


def test_tracker_does_nothing_when_turned_off_but_still_records(qapp, tmp_path):
    from voidtrace.trade_toast import TradeToast, TradeTracker

    account = FakeAccount([order("r5", "id-hp", rank=5, quantity=3)])
    account.signed_in = False
    history = TradeHistory(tmp_path / "t.jsonl")
    tracker = TradeTracker(account, CATALOG, TradeToast(QRect(0, 0, 800, 600)), is_enabled=lambda: False,
                           history=history)
    tracker.executor.submit = lambda fn, *a: fn(*a)
    for line in REAL_TRADE:
        tracker.feed(line)
    assert account.calls == []
    assert [r.trade.partner for r in history.load()] == ["TraderJoe"]
    tracker.shutdown()


# --- history -----------------------------------------------------------------------------------------

def test_history_round_trip_skips_bad_lines(tmp_path):
    history = TradeHistory(tmp_path / "trades.jsonl")
    sold = Trade("A", (TradeItem("Hollow Point", 1, 5),), (TradeItem("Platinum", 10),))
    bought = Trade("B", (TradeItem("Platinum", 25),), (TradeItem("Forma Blueprint", 2),))
    history.add(sold, datetime(2026, 10, 11, 9, 0))
    with open(history.path, "a", encoding="utf-8") as fh:
        fh.write('not json\n{"time": "x"}\n')
    history.add(bought, datetime(2026, 10, 12, 9, 0))
    records = history.load()
    assert [r.trade for r in records] == [sold, bought]
    assert [r.plat for r in records] == [10, -25]


def test_missing_history_is_empty(tmp_path):
    assert TradeHistory(tmp_path / "none.jsonl").load() == []


def test_totals_since():
    def rec(day, got=0, given=0):
        return TradeRecord(datetime(2026, 10, day, 12), Trade("x", (TradeItem("Platinum", given),) if given else (),
                                                              (TradeItem("Platinum", got),) if got else ()))
    records = [rec(1, got=100), rec(10, got=30), rec(12, given=20), rec(12, got=5)]
    assert totals(records) == Totals(135, 20, 4)
    assert totals(records, since=datetime(2026, 10, 10)) == Totals(35, 20, 3)
    assert totals(records, since=datetime(2026, 10, 12)).net == -15
