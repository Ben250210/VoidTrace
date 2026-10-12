import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace import settings
from voidtrace.alerts import Alert, AlertWatcher, add_alert, check, load_alerts, remove_alert
from voidtrace.sellers import SellerOrder


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # never touch the user's real settings
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def seller(name, price, rank=None, suspicious=False, rep=5):
    return SellerOrder(name, "ingame", price, 1, rank, rep, "", suspicious)


ALERT = Alert("volt_prime_set", "Volt Prime Set", 55)


def test_check_finds_the_cheapest_at_or_under():
    rows = [seller("A", 60), seller("B", 55), seller("C", 50, rep=1), seller("D", 50, rep=9)]
    assert check(ALERT, rows).ingame_name == "D"


def test_check_nothing_cheap_enough():
    assert check(ALERT, [seller("A", 56)]) is None


def test_check_skips_me_and_troll_prices():
    rows = [seller("Me", 40), seller("Troll", 1, suspicious=True), seller("Real", 54)]
    assert check(ALERT, rows, me="me").ingame_name == "Real"


def test_check_rank():
    alert = Alert("hollow_point", "Hollow Point", 20, rank=5, ranked=True)
    assert check(alert, [seller("R0", 5, rank=0), seller("R5", 18, rank=5)]).ingame_name == "R5"


def test_alerts_round_trip_and_replace():
    add_alert(ALERT)
    add_alert(Alert("hollow_point", "Hollow Point", 20, 5, True))
    add_alert(Alert("volt_prime_set", "Volt Prime Set", 50))  # same item: replaces
    assert load_alerts() == [Alert("hollow_point", "Hollow Point", 20, 5, True), Alert("volt_prime_set", "Volt Prime Set", 50)]
    remove_alert(Alert("hollow_point", "Hollow Point", 0, 5))
    assert [a.slug for a in load_alerts()] == ["volt_prime_set"]


def test_bad_saved_alert_is_skipped():
    settings.put("alerts", [{"slug": "x"}, {"slug": "y", "name": "Y", "max_price": "7"}])
    assert load_alerts() == [Alert("y", "Y", 7)]


class FakeSellers:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def sellers(self, slug, rank=None, include_online=True):
        self.calls.append(slug)
        return self.rows


def test_watcher_announces_each_seller_and_price_once(qapp):
    fake = FakeSellers([seller("B", 50)])
    watcher = AlertWatcher(fake, load=lambda: [ALERT], sleep=lambda s: None)
    found = []
    watcher.found.connect(lambda alert, s: found.append((alert.slug, s.ingame_name, s.platinum)))
    watcher.run()
    watcher.run()
    fake.rows = [seller("B", 45)]  # cheaper again: worth telling
    watcher.run()
    assert found == [("volt_prime_set", "B", 50), ("volt_prime_set", "B", 45)]
    watcher.shutdown()


def test_watcher_keeps_going_when_one_item_fails(qapp):
    class Flaky(FakeSellers):
        def sellers(self, slug, rank=None, include_online=True):
            if slug == "bad":
                raise RuntimeError("timeout")
            return self.rows

    alerts = [Alert("bad", "Bad", 10), ALERT]
    watcher = AlertWatcher(Flaky([seller("B", 50)]), load=lambda: alerts, sleep=lambda s: None)
    found = []
    watcher.found.connect(lambda alert, s: found.append(alert.slug))
    watcher.run()
    assert found == ["volt_prime_set"]
    watcher.shutdown()


def test_market_window_adds_an_alert(qapp):
    from voidtrace.items import CatalogItem
    from voidtrace.market_window import MarketWindow

    class Acc:
        signed_in = False
        ingame_name = None

    item = CatalogItem("Volt Prime Set", "volt_prime_set", None, ("set",), "id-v")
    window = MarketWindow([item], FakeSellers([]), account=Acc())
    window.item = item
    window.rows = [seller("A", 60), seller("Troll", 2, suspicious=True), seller("B", 58)]
    assert window.suggested_alert_price() == 57
    changed = []
    window.alerts_changed.connect(lambda: changed.append(1))
    window.add_alert(57)
    assert load_alerts() == [Alert("volt_prime_set", "Volt Prime Set", 57, None, False)]
    assert changed and window.alerts_panel.table.rowCount() == 1
    window.alerts_panel.delete(load_alerts()[0])
    assert load_alerts() == [] and window.alerts_panel.table.rowCount() == 0
    window.shutdown()
