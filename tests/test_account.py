"""Account + order posting, against a fake Warframe.Market (no network, no real credential store)."""

import json

import pytest

from voidtrace.account import (
    Account,
    MarketError,
    NotSignedIn,
    TokenStore,
    TopOrder,
    device_id,
    suggested_price,
    top_orders,
)


class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, key):
        return self.data.get((service, key))

    def set_password(self, service, key, value):
        self.data[(service, key)] = value

    def delete_password(self, service, key):
        self.data.pop((service, key), None)


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.ok = 200 <= status < 300
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeHTTP:
    """Records requests; answers like Warframe.Market."""

    def __init__(self):
        self.calls = []
        self.token = "abc.def.ghi"
        self.orders = []
        self.top = {"sell": [], "buy": []}

    def request(self, method, url, headers=None, json=None):
        self.calls.append((method, url, headers or {}, json))
        if url.endswith("/auth/signin"):
            if json["password"] != "hunter2":
                return Resp(400, {"error": {"password": ["app.account.password_invalid"]}})
            return Resp(200, {"payload": {"user": {"ingame_name": "Ben250210"}}},
                        {"Authorization": f"JWT {self.token}"})
        if (headers or {}).get("Authorization") != f"Bearer {self.token}":
            return Resp(401, {"error": "unauthorized"})
        if method == "POST" and url == "/order":
            order = {"id": f"o{len(self.orders) + 1}", "itemId": json["itemId"], "type": json["type"],
                     "platinum": json["platinum"], "quantity": json["quantity"], "rank": json.get("rank"),
                     "visible": json["visible"]}
            self.orders.append(order)
            return Resp(201, {"data": order})
        if method == "GET" and url == "/orders/my":
            return Resp(200, {"data": self.orders})
        if method == "PATCH":
            order = next(o for o in self.orders if url.endswith(o["id"]))
            order.update(json)
            return Resp(200, {"data": order})
        if method == "DELETE":
            self.orders = [o for o in self.orders if not url.endswith(o["id"])]
            return Resp(200, {"data": None})
        return Resp(200, None)

    def get(self, path):
        assert "/top" in path
        self.calls.append(("GET", path, {}, None))
        return self.top


def account(http=None, keyring=None):
    return Account(http or FakeHTTP(), TokenStore(keyring or MemoryKeyring()), device="test-device")


def test_sign_in_stores_token_not_password():
    kr = MemoryKeyring()
    http = FakeHTTP()
    acc = account(http, kr)
    assert acc.sign_in("me@example.com", "hunter2") == "Ben250210"
    assert acc.signed_in and acc.token == "abc.def.ghi"           # "JWT " prefix stripped
    assert "hunter2" not in json.dumps(list(kr.data.values()))    # password never stored
    method, url, headers, body = http.calls[0]
    assert url == "https://api.warframe.market/v1/auth/signin"
    assert headers["Authorization"] == "JWT" and body["auth_type"] == "header" and body["device_id"] == "test-device"
    # A new session on this PC picks the token up from the credential store.
    assert account(http, kr).signed_in


def test_wrong_password_is_a_plain_error():
    acc = account()
    with pytest.raises(MarketError, match="did not accept"):
        acc.sign_in("me@example.com", "wrong")
    assert not acc.signed_in


def test_create_list_update_delete_orders():
    http = FakeHTTP()
    acc = account(http)
    acc.sign_in("me", "hunter2")
    order = acc.create_order("5f4ed3f9d5c36d0086f55b0a", "sell", 9, quantity=2, rank=3)
    method, url, headers, body = http.calls[-1]
    assert (method, url) == ("POST", "/order")
    assert headers["Authorization"] == "Bearer abc.def.ghi"
    assert body == {"itemId": "5f4ed3f9d5c36d0086f55b0a", "type": "sell", "platinum": 9, "quantity": 2,
                    "visible": True, "rank": 3}
    assert order.platinum == 9 and order.rank == 3
    assert [o.id for o in acc.my_orders()] == ["o1"]
    assert acc.update_order("o1", platinum=8).platinum == 8
    acc.delete_order("o1")
    assert acc.my_orders() == []


def test_unranked_item_sends_no_rank():
    http = FakeHTTP()
    acc = account(http)
    acc.sign_in("me", "hunter2")
    acc.create_order("item", "buy", 4)
    assert "rank" not in http.calls[-1][3]


def test_bad_order_values_are_refused_before_sending():
    acc = account()
    acc.sign_in("me", "hunter2")
    with pytest.raises(ValueError):
        acc.create_order("item", "sell", 0)
    with pytest.raises(ValueError):
        acc.create_order("item", "trade", 5)


def test_expired_session_signs_you_out():
    kr = MemoryKeyring()
    http = FakeHTTP()
    acc = account(http, kr)
    acc.sign_in("me", "hunter2")
    http.token = "rotated"  # server no longer accepts ours
    with pytest.raises(NotSignedIn, match="expired"):
        acc.my_orders()
    assert not acc.signed_in and kr.data == {}


def test_actions_need_sign_in():
    with pytest.raises(NotSignedIn):
        account().create_order("item", "sell", 5)


def test_sign_out_forgets_the_token():
    kr = MemoryKeyring()
    acc = account(keyring=kr)
    acc.sign_in("me", "hunter2")
    acc.sign_out()
    assert not acc.signed_in and kr.data == {}


def test_device_id_is_created_once(tmp_path):
    path = tmp_path / "device.json"
    assert device_id(path) == device_id(path)


# --- top orders + pricing ------------------------------------------------------------------

def top(sell=(), buy=()):
    mk = lambda name, p: TopOrder(name, p, 1, 3, 10, "ingame")  # noqa: E731
    return {"sell": [mk(n, p) for n, p in sell], "buy": [mk(n, p) for n, p in buy]}


def test_top_orders_asks_for_one_rank():
    http = FakeHTTP()
    http.top = {"sell": [{"platinum": 9, "quantity": 2, "rank": 3,
                          "user": {"ingameName": "August159", "reputation": 34, "status": "ingame"}}], "buy": []}
    result = top_orders(http, "necramech_fury", rank=3)
    assert http.calls[-1][1] == "/orders/item/necramech_fury/top?rank=3"
    assert result["sell"][0] == TopOrder("August159", 9, 2, 3, 34, "ingame")


@pytest.mark.parametrize("side,offset,expected", [
    ("sell", 0, 9),    # match the cheapest seller
    ("sell", -1, 8),   # undercut by one
    ("sell", 3, 12),
    ("buy", 0, 7),     # match the highest buyer
    ("buy", 1, 8),     # outbid by one
])
def test_suggested_price(side, offset, expected):
    assert suggested_price(top(sell=[("a", 9), ("b", 14)], buy=[("c", 5), ("d", 7)]), side, offset) == expected


def test_suggested_price_never_below_one_and_none_when_empty():
    assert suggested_price(top(sell=[("a", 1)]), "sell", -5) == 1
    assert suggested_price(top(), "sell") is None


def test_suggested_price_ignores_your_own_listing():
    assert suggested_price(top(sell=[("Ben250210", 6), ("a", 9)]), "sell", 0, exclude="ben250210") == 9
