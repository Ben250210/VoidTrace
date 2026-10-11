"""Riven auctions on your account, against a fake Warframe.Market v1 (shaped like the live answers
recorded on 2026-10-11 with one hidden test auction)."""

import copy
import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from test_account import MemoryKeyring, Resp
from voidtrace import rivens
from voidtrace.account import Account, MarketError, NotSignedIn, TokenStore
from voidtrace.rivens import PriceCheck, RivenAttr, RivenDraft, RivenService

FIXTURES = Path(__file__).parent / "fixtures" / "rivens"
V1 = "https://api.warframe.market/v1"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return rivens.RivenCatalog(rivens.parse_weapons(_load("weapons.json")["data"]),
                               rivens.parse_stats(_load("attributes.json")["data"]))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeV1:
    """Sign-in plus the v1 auction endpoints. Like the live site, create ignores `visible` (new
    auctions come back hidden) and rejects names that do not fit the buffs."""

    def __init__(self, valid_names=("critacan", "saticron")):
        self.calls = []
        self.token = "abc.def.ghi"
        self.auctions = []
        self.valid_names = set(valid_names)
        self.kulstar = _load("auctions_kulstar.json")["payload"]["auctions"]

    def request(self, method, url, headers=None, json=None):
        self.calls.append((method, url, headers or {}, copy.deepcopy(json)))
        if url.endswith("/auth/signin"):
            return Resp(200, {"payload": {"user": {"ingame_name": "BenMonke"}}}, {"Authorization": f"JWT {self.token}"})
        if (headers or {}).get("Authorization") != f"JWT {self.token}":
            return Resp(401, {"error": "unauthorized"})
        if method == "GET" and url == f"{V1}/profile/BenMonke/auctions":
            return Resp(200, {"payload": {"auctions": self.auctions}})
        if method == "POST" and url == f"{V1}/auctions/create":
            if json["item"]["name"] not in self.valid_names:
                return Resp(400, {"error": {"item": {"name": ["app.form.invalid"]}}})
            auction = {"id": f"a{len(self.auctions) + 1}", "visible": False, "closed": False,
                       "owner": "66bf2babb7f0430008996b05", "is_direct_sell": json["starting_price"] == json["buyout_price"],
                       "top_bid": None, "note_raw": json["note"],
                       **{k: json[k] for k in ("starting_price", "buyout_price", "minimal_reputation")},
                       "item": json["item"]}
            self.auctions.append(auction)
            return Resp(200, {"payload": {"auction": auction}})
        if method == "PUT" and url.endswith("/close"):
            auction_id = url.split("/")[-2]
            self.auctions = [a for a in self.auctions if a["id"] != auction_id]
            return Resp(200, {"payload": {"auction_id": auction_id}})
        if method == "PUT" and "/auctions/entry/" in url:
            auction = next(a for a in self.auctions if a["id"] == url.split("/")[-1])
            auction.update({k: v for k, v in json.items() if k != "note"}, note_raw=json["note"])
            return Resp(200, {"payload": {"auction": auction}})
        return Resp(404, {"error": "not found"})

    def get_v1(self, path):
        return {"auctions": self.kulstar}


def signed_in(http=None):
    http = http or FakeV1()
    account = Account(http, TokenStore(MemoryKeyring()), device="test")
    account.sign_in("me", "hunter2")
    return account, http


DRAFT = RivenDraft(weapon="kulstar", name="critacan",
                   attrs=(RivenAttr("critical_chance", 120.5, True), RivenAttr("multishot", 80.0, True)),
                   mr=12, rolls=3, rank=8, polarity="naramon")


# --- account --------------------------------------------------------------------------------

def test_create_sends_the_riven_and_makes_it_visible():
    account, http = signed_in()
    auction = account.create_riven_auction(DRAFT, buyout=300, minimal_reputation=5, note="pm me")
    method, url, headers, body = next(c for c in http.calls if c[1].endswith("/auctions/create"))
    assert headers["Authorization"] == "JWT abc.def.ghi"
    assert body["buyout_price"] == body["starting_price"] == 300
    assert body["item"] == {"type": "riven", "weapon_url_name": "kulstar", "name": "critacan", "mastery_level": 12,
                            "re_rolls": 3, "mod_rank": 8, "polarity": "naramon",
                            "attributes": [{"url_name": "critical_chance", "value": 120.5, "positive": True},
                                           {"url_name": "multishot", "value": 80.0, "positive": True}]}
    # The site created it hidden; a follow-up edit made it visible as asked.
    assert auction.visible and http.calls[-1][0] == "PUT"
    assert auction.seller == "BenMonke" and auction.note == "pm me" and auction.minimal_reputation == 5


def test_create_hidden_needs_no_follow_up_edit():
    account, http = signed_in()
    auction = account.create_riven_auction(DRAFT, buyout=300, visible=False)
    assert not auction.visible and http.calls[-1][1].endswith("/auctions/create")


def test_wrong_riven_name_is_explained():
    account, _ = signed_in()
    bad = RivenDraft(**{**DRAFT.__dict__, "name": "crita-acritis"})
    with pytest.raises(MarketError, match="name does not match"):
        account.create_riven_auction(bad, buyout=300)


def test_create_checks_prices_and_fields_first():
    account, http = signed_in()
    with pytest.raises(ValueError):
        account.create_riven_auction(DRAFT, buyout=100, starting=200)
    with pytest.raises(ValueError):
        account.create_riven_auction(RivenDraft(weapon="kulstar"), buyout=100)
    assert not any(c[1].endswith("/auctions/create") for c in http.calls)


def test_list_edit_and_close():
    account, http = signed_in()
    account.create_riven_auction(DRAFT, buyout=300)
    [mine] = account.my_auctions()
    assert mine.weapon == "kulstar" and mine.buyout == 300
    cheaper = account.update_auction(mine, buyout_price=250)
    body = http.calls[-1][3]
    # Everything else is sent unchanged; a direct sale's starting price follows the buyout down.
    assert body == {"buyout_price": 250, "starting_price": 250, "visible": True, "minimal_reputation": 0, "note": ""}
    assert cheaper.buyout == 250
    account.close_auction(mine.id)
    assert http.calls[-1][1] == f"{V1}/auctions/entry/{mine.id}/close"
    assert account.my_auctions() == []


def test_expired_session_on_an_auction_call():
    account, http = signed_in()
    http.token = "rotated"
    with pytest.raises(NotSignedIn):
        account.my_auctions()
    assert not account.signed_in


def test_valid_names(catalog):
    assert rivens.valid_names(catalog, ["critical_chance", "multishot"]) == {"critacan", "saticron"}
    assert "critacron" in rivens.valid_names(catalog, ["critical_chance"])
    assert len(rivens.valid_names(catalog, ["critical_chance", "multishot", "toxin_damage"])) == 6
    assert rivens.valid_names(catalog, []) == set()


# --- window ------------------------------------------------------------------------------

def _wait(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate()


def _window(catalog, account=None, http=None):
    from voidtrace.riven_window import RivenWindow

    if account is None:
        account, http = signed_in()
    return RivenWindow(catalog, RivenService(http), account=account), http


def test_sell_from_the_price_check(qapp, catalog):
    window, http = _window(catalog)
    window.open_weapon("kulstar")
    window.load_draft(DRAFT)
    window.price_panel.run_check()
    _wait(qapp, lambda: window.price_panel.check is not None)
    window.price_panel.sell_button.click()
    panel = window.sell_panel
    assert window.tabs.currentWidget() is panel
    assert panel.buyout.value() == round(window.price_panel.check.estimate)
    assert "Kulstar Critacan" in panel.summary.text() and panel.name_warning.text() == ""
    panel.buyout.setValue(321)
    panel.post()
    _wait(qapp, lambda: panel.result.text().startswith("Posted"))
    assert "321p" in panel.result.text()
    assert http.auctions[0]["buyout_price"] == 321 and http.auctions[0]["visible"] is True
    window.shutdown()


def test_sell_warns_about_a_name_that_does_not_fit(qapp, catalog):
    window, _ = _window(catalog)
    window.open_sell(RivenDraft(**{**DRAFT.__dict__, "name": "crita-acritis"}), None)
    assert "will refuse it" in window.sell_panel.name_warning.text()
    assert "critacan" in window.sell_panel.name_warning.text()
    window.shutdown()


def test_signed_out_sell_button_asks_to_sign_in(qapp, catalog):
    account = Account(FakeV1(), TokenStore(MemoryKeyring()), device="test")
    window, _ = _window(catalog, account, FakeV1())
    window.open_sell(DRAFT, PriceCheck(100.0, 50, 200, 5, "same stats, no curse", ()))
    assert window.sell_panel.post_button.text() == "Sign in to post"
    assert window.account_button.text() == "Sign in to Warframe.Market"
    window.shutdown()


def test_my_rivens_toggle_reprice_and_close(qapp, catalog):
    window, http = _window(catalog)
    window.account.create_riven_auction(DRAFT, buyout=5000)
    window.tabs.setCurrentWidget(window.my_rivens)
    panel = window.my_rivens
    _wait(qapp, lambda: panel.table.rowCount() == 1)
    assert panel.status.text() == "1 rivens"
    panel.toggle_visible(panel.shown[0])
    _wait(qapp, lambda: panel.shown and not panel.shown[0].visible)
    assert "1 hidden" in panel.status.text()
    panel.reprice(panel.shown[0])
    # The result stays readable after the list reloads.
    _wait(qapp, lambda: panel.shown and panel.shown[0].buyout < 5000 and "rivens, 1 hidden" in panel.status.text())
    assert "Kulstar Critacan: now" in panel.status.text()
    panel.close_auction(panel.shown[0])
    _wait(qapp, lambda: panel.table.rowCount() == 0 and "no riven auctions" in panel.status.text())
    window.shutdown()
