import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from voidtrace import rivens
from voidtrace.ui.riven_find import disposition_dots
from voidtrace.ui.rivens import RivenSection
from voidtrace.rivens import RivenAttr, RivenDraft, RivenService

FIXTURES = Path(__file__).parent / "fixtures" / "rivens"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    from test_account import MemoryKeyring
    import voidtrace.account as account_module

    monkeypatch.setattr(account_module.TokenStore, "__init__",
                        lambda self, backend=None: setattr(self, "_kr", backend or MemoryKeyring()))


class FakeClient:
    """Answers riven searches with the saved Kulstar page, filtered the way the site filters."""

    def __init__(self):
        self.paths = []
        self.auctions = _load("auctions_kulstar.json")["payload"]["auctions"]

    def get_v1(self, path):
        self.paths.append(path)
        from urllib.parse import parse_qs, urlparse

        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        rows = self.auctions
        if "positive_stats" in q:
            want = set(q["positive_stats"].split(","))
            rows = [a for a in rows if want <= {x["url_name"] for x in a["item"]["attributes"] if x["positive"]}]
        if q.get("negative_stats") == "none":
            rows = [a for a in rows if all(x["positive"] for x in a["item"]["attributes"])]
        return {"auctions": rows}


def _window():
    catalog = rivens.RivenCatalog(rivens.parse_weapons(_load("weapons.json")["data"]),
                                  rivens.parse_stats(_load("attributes.json")["data"]))
    client = FakeClient()
    return RivenSection(catalog, RivenService(client)), client


def _wait(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_disposition_dots():
    assert disposition_dots(0.5) == "●○○○○"
    assert disposition_dots(1.0) == "●●●○○"
    assert disposition_dots(1.3) == "●●●●○"
    assert disposition_dots(1.45) == "●" * 5


def test_search_a_weapon_and_copy_a_whisper(qapp):
    window, client = _window()
    window._on_search("kuls")
    assert window.suggestions.item(0).text() == "Kulstar"
    window._pick_first_suggestion()
    panel = window.find_panel
    _wait(qapp, lambda: panel.table.rowCount() > 0)
    assert "Kulstar" in window.title.text() and "1.30" in window.title.text()
    assert all(a.status in ("ingame", "online") for a in panel.auctions)  # offline hidden by default
    # Only stats a pistol can roll are offered.
    stats = {panel.buffs[0].itemData(i) for i in range(panel.buffs[0].count())}
    assert "multishot" in stats and "range" not in stats
    panel.copy_row(0)
    first = panel.auctions[0]
    assert QApplication.clipboard().text().startswith(f"/w {first.seller} Hi! I'd like to buy your Kulstar ")
    assert QApplication.clipboard().text().endswith("(warframe.market through VoidTrace)")
    assert first.seller in window.toast.text()
    window.shutdown()


def test_filters_go_into_the_search(qapp):
    window, client = _window()
    window.open_weapon("kulstar")
    panel = window.find_panel
    panel.offline.setChecked(True)
    panel.buffs[0].setCurrentIndex(panel.buffs[0].findData("critical_chance"))
    panel.curse.setCurrentIndex(panel.curse.findData("none"))
    wanted = "/auctions/search?type=riven&weapon_url_name=kulstar&sort_by=price_asc" \
             "&positive_stats=critical_chance&negative_stats=none"
    # Searches run on two worker threads, so they can reach the client in either order; the
    # panel only shows the newest one.
    _wait(qapp, lambda: wanted in client.paths and panel.auctions and panel.status.text().endswith("cheapest."))
    assert all(a.negative is None and "critical_chance" in a.positives for a in panel.auctions)
    window.shutdown()


def test_price_check_from_a_typed_riven(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    panel = window.price_panel
    draft = RivenDraft(weapon="kulstar", name="crita-acritis",
                       attrs=(RivenAttr("critical_chance", 120.5, True), RivenAttr("multishot", 80.0, True),
                              RivenAttr("zoom", -30.0, False)), mr=12, rolls=3)
    window.load_draft(draft)
    assert window.pages.currentWidget() is panel
    assert panel.draft() == draft
    panel.run_check()
    _wait(qapp, lambda: panel.check is not None)
    assert panel.result.text().startswith("About ")
    assert panel.table.rowCount() == panel.check.count
    assert panel.sell_button.isEnabled()
    window.shutdown()


def test_curse_typed_as_plain_number_becomes_negative_except_recoil(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    curse = window.price_panel.rows[-1]
    curse.box.setCurrentIndex(curse.box.findData("zoom"))
    curse.value.setValue(30)
    assert curse.attr() == RivenAttr("zoom", -30.0, False)
    curse.box.setCurrentIndex(curse.box.findData("recoil"))
    assert curse.attr() == RivenAttr("recoil", 30.0, False)
    window.shutdown()


def test_typing_the_name_fills_in_the_buffs(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    panel = window.price_panel
    panel.name.setText("Saticron")
    panel.fill_from_name()
    assert [row.box.currentData() for row in panel.rows[:3]] == ["multishot", "critical_chance", None]
    assert "filled in from the name" in panel.name_note.text()


def test_a_name_that_does_not_fit_the_buffs_is_flagged(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    panel = window.price_panel
    panel.name.setText("critacan")
    panel.fill_from_name()
    panel.rows[0].box.setCurrentIndex(panel.rows[0].box.findData("toxin_damage"))
    assert "does not match" in panel.name_note.text()


def test_mouse_wheel_does_not_change_an_unclicked_dropdown(qapp):
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent

    window, _ = _window()
    window.open_weapon("kulstar")
    box = window.price_panel.rows[0].box
    box.setCurrentIndex(box.findData("puncture_damage"))
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(box, wheel)
    assert box.currentData() == "puncture_damage"


def test_reading_a_screenshot_fills_the_price_check(qapp, monkeypatch):
    from voidtrace import riven_ocr

    window, _ = _window()
    draft = RivenDraft(weapon="kulstar", name="critacan",
                       attrs=(RivenAttr("critical_chance", 12.1, True), RivenAttr("multishot", 9.4, True)), mr=9)
    monkeypatch.setattr(riven_ocr, "read_riven", lambda frame, catalog: riven_ocr.RivenRead(draft))
    window.read_from_frame(object())
    _wait(qapp, lambda: window.price_panel.check is not None)
    assert window.price_panel.draft().attrs == draft.attrs
    assert window.price_panel.name_note.text().startswith("Read from the screen")
    window.shutdown()


def test_reading_a_screen_without_a_riven_says_so(qapp, monkeypatch):
    from voidtrace import riven_ocr

    window, _ = _window()
    monkeypatch.setattr(riven_ocr, "read_riven",
                        lambda frame, catalog: riven_ocr.RivenRead(RivenDraft(), ["No riven card found."]))
    window.read_from_frame(object())
    _wait(qapp, lambda: "No riven card" in window.toast.text())
    window.shutdown()


def test_clean_filter_searches_unrolled_and_labels_them(qapp):
    window, client = _window()
    window.open_weapon("kulstar")
    panel = window.find_panel
    panel.rolls.setCurrentIndex(panel.rolls.findData(rivens.CLEAN))
    _wait(qapp, lambda: any("re_rolls_max=0" in p for p in client.paths) and panel.status.text().endswith("cheapest."))
    clean = [r for r, a in enumerate(panel.auctions) if a.rolls == 0]
    assert clean and panel.table.item(clean[0], 5).text() == "0 \u00b7 clean"
    window.shutdown()


def test_price_check_ignores_the_rolls_filter(qapp):
    window, client = _window()
    window.open_weapon("kulstar")
    window.find_panel.rolls.setCurrentIndex(window.find_panel.rolls.findData(rivens.ROLLED))
    # Let the Find tab's own (filtered) search finish first, so only the price check is recorded.
    _wait(qapp, lambda: any("re_rolls_min=1" in p for p in client.paths)
          and window.find_panel.status.text().endswith("cheapest."))
    panel = window.price_panel
    panel.name.setText("critacan")
    panel.fill_from_name()
    calls = []
    search = window.service.search
    window.service.search = lambda *args, **kwargs: (calls.append(kwargs), search(*args, **kwargs))[1]
    panel.run_check()
    _wait(qapp, lambda: panel.check is not None)
    assert calls and all(kwargs.get("rolls") is None for kwargs in calls)
    window.shutdown()


def test_name_change_resets_the_old_values(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    panel = window.price_panel
    panel.name.setText("critacan")
    panel.fill_from_name()
    panel.rows[0].value.setValue(120.5)
    panel.name.setText("saticron")  # buff 1 becomes multishot: 120.5 was the crit chance value
    panel.fill_from_name()
    assert panel.rows[0].box.currentData() == "multishot" and panel.rows[0].value.value() == 0.0


def test_grades_show_beside_each_value(qapp):
    window, _ = _window()
    window.open_weapon("kulstar")
    panel = window.price_panel
    panel.name.setText("critacan")
    panel.fill_from_name()
    cc = rivens.expected_value(window.catalog, window.catalog.by_weapon["kulstar"], "critical_chance", True, 2, False, 0)
    panel.rows[0].value.setValue(round(cc * 1.1, 2))   # the best possible crit chance roll
    panel.rows[1].value.setValue(500.0)                # impossible multishot: flagged, not graded
    assert panel.rows[0].grade.text() == "A+"
    assert panel.rows[1].grade.text() == "?"
    assert panel.table.horizontalHeaderItem(6).text() == "Grade"
    window.shutdown()
