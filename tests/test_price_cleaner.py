import time

import pytest

from voidtrace.price_cleaner import (
    JsonPriceCache,
    PriceService,
    PriceStats,
    iqr_filter,
    select_sell_prices,
    summarize,
    trade_price,
    weighted_median,
)


def order(plat, status="ingame", type_="sell", visible=True):
    return {"type": type_, "platinum": plat, "visible": visible, "user": {"status": status}}


def stats_payload(buckets_48h=(), days_90=()):
    def bucket(median, volume):
        return {"median": median, "volume": volume}

    return {"statistics_closed": {"48hours": [bucket(*b) for b in buckets_48h], "90days": [bucket(*b) for b in days_90]}}


# Real in-game Burston Prime Blueprint asks seen on 2026-10-10: a cheap cluster and a
# lingering overpriced cluster. The old ask-median logic priced this at 14.5p.
BURSTON_ASKS = [2, 5, 5, 6, 6, 6, 7, 8, 9, 10, 10, 19, 29, 29, 30, 40]
# Its closed trades in the same 48 hours: 36 trades around 3-5p.
BURSTON_TRADES = [(5, 2), (4, 2), (3, 3), (3.5, 2), (3, 1), (5, 1), (5, 1), (4, 2), (3, 2), (4, 3), (3, 1),
                  (4.5, 2), (4, 1), (5, 1), (5, 1), (4.5, 2), (5, 1), (2.5, 2), (5, 1), (5, 1), (5, 1), (3, 3)]


# --- outlier filter -----------------------------------------------------------------

def test_iqr_removes_lowball_and_gouge():
    kept, outliers = iqr_filter([13, 25, 27, 28, 30, 30, 30, 32, 35, 254])
    assert outliers == [13.0, 254.0]
    assert kept == [25, 27, 28, 30, 30, 30, 32, 35]


def test_iqr_log_scale_catches_lowballs_a_linear_fence_misses():
    # Linear fences: Q1 - 1.5*IQR is far below zero, so the 1p troll survives.
    kept, outliers = iqr_filter([1, 20, 22, 24, 25, 26, 28, 30, 60, 75])
    assert 1.0 in outliers


def test_iqr_leaves_tiny_samples_alone():
    kept, outliers = iqr_filter([1, 500, 3])
    assert outliers == []
    assert kept == [1, 3, 500]


def test_iqr_floor_keeps_neighbours_of_identical_cluster():
    kept, outliers = iqr_filter([28, 30, 30, 30, 30, 30, 30, 32, 90])
    assert outliers == [90.0]
    assert 28.0 in kept and 32.0 in kept


def test_iqr_floor_is_at_least_one_plat_for_cheap_items():
    kept, outliers = iqr_filter([2, 3, 3, 3, 3, 3, 4])
    assert outliers == []


# --- trade statistics -----------------------------------------------------------------

def test_weighted_median():
    assert weighted_median([10, 1, 5], [1, 1, 10]) == 5
    assert weighted_median([3, 5], [3, 1]) == 3


def test_trade_price_uses_48h_when_enough_volume():
    price, volume, basis = trade_price(stats_payload(BURSTON_TRADES))
    assert (price, volume, basis) == (4.0, 36, "trades_48h")


def test_trade_price_falls_back_to_last_7_days():
    payload = stats_payload([(9, 2)], days_90=[(50, 9)] * 3 + [(20, 3)] * 7)
    price, volume, basis = trade_price(payload)
    assert basis == "trades_7d" and volume == 21 and price == 20


def test_trade_price_none_when_illiquid_or_missing():
    assert trade_price(stats_payload([(9, 1)], [(9, 1)])) == (None, 0, None)
    assert trade_price(None) == (None, 0, None)


# --- listings -------------------------------------------------------------------------

def test_prefers_ingame_sellers_and_ignores_buys_and_hidden():
    orders = [order(p) for p in (30, 31, 32, 33, 34)] + [
        order(5, status="offline"),
        order(99, type_="buy"),
        order(1, visible=False),
    ]
    prices, tier = select_sell_prices(orders)
    assert tier == "ingame"
    assert prices == [30, 31, 32, 33, 34]


def test_widens_to_online_then_all_when_too_few():
    orders = [order(30), order(31, "online"), order(32, "online"), order(33, "online"), order(34, "online")]
    assert select_sell_prices(orders)[1] == "online"
    orders = [order(30), order(31, "offline"), order(32, "offline"), order(33, "offline"), order(34, "offline")]
    prices, tier = select_sell_prices(orders)
    assert tier == "all" and len(prices) == 5


# --- summarize --------------------------------------------------------------------------

def test_burston_regression_prices_from_trades_not_ask_median():
    stats = summarize("burston_prime_blueprint", [order(p) for p in BURSTON_ASKS], stats_payload(BURSTON_TRADES))
    assert stats.price == 4.0 and stats.basis == "trades_48h" and stats.trade_volume == 36
    assert stats.sell_now == 5.0  # median of the 5 cheapest in-game asks


def test_burston_without_trade_history_uses_cheapest_asks():
    stats = summarize("burston_prime_blueprint", [order(p) for p in BURSTON_ASKS])
    assert stats.basis == "listings" and stats.price == 5.0


def test_summarize_reports_outliers_and_range():
    orders = [order(p) for p in (13, 25, 27, 28, 30, 30, 30, 32, 35, 254)]
    stats = summarize("nekros_prime_blueprint", orders, now=1000.0)
    assert stats.n_orders == 10 and stats.n_outliers == 2
    assert (stats.low, stats.high) == (25.0, 35.0)
    assert stats.sell_now == 28.0 and stats.source == "ingame"


def test_summarize_with_trades_but_no_listings():
    stats = summarize("x", [], stats_payload(BURSTON_TRADES))
    assert stats.price == 4.0 and stats.sell_now is None and stats.source == "none"


def test_summarize_with_nothing_returns_none():
    assert summarize("x", [order(10, type_="buy")]) is None


# --- caching / service --------------------------------------------------------------------

def make_stats(slug="nekros_prime_blueprint", price=30.0, fetched_at=None):
    return PriceStats(slug, price, "trades_48h", 40, 31.0, 28.0, 35.0, 10, 2, "ingame", fetched_at or time.time())


def test_json_cache_roundtrip(tmp_path):
    cache = JsonPriceCache(tmp_path / "prices.json")
    assert cache.get("nekros_prime_blueprint") is None
    stats = make_stats()
    cache.put(stats)
    assert cache.get("nekros_prime_blueprint") == stats


def test_json_cache_ignores_entries_from_old_schema(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text('{"x": {"slug": "x", "median": 5}}')
    assert JsonPriceCache(path).get("x") is None


class FakeClient:
    def __init__(self, orders=None, statistics=None, error=None, stats_error=None):
        self.orders, self.statistics, self.error, self.stats_error = orders, statistics, error, stats_error
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.orders

    def get_v1(self, path):
        self.calls.append(path)
        if self.stats_error:
            raise self.stats_error
        return self.statistics


def test_service_uses_fresh_cache_without_network(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    cache.put(make_stats())
    client = FakeClient(error=AssertionError("should not be called"))
    assert PriceService(client, cache).get_price("nekros_prime_blueprint").price == 30.0
    assert client.calls == []


def test_service_fetches_orders_and_trades_then_caches(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    client = FakeClient([order(p) for p in BURSTON_ASKS], stats_payload(BURSTON_TRADES))
    stats = PriceService(client, cache).get_price("burston_prime_blueprint")
    # Fetched concurrently, so order is not fixed.
    assert sorted(client.calls) == ["/items/burston_prime_blueprint/statistics", "/orders/item/burston_prime_blueprint"]
    assert stats.price == 4.0 and not stats.stale
    assert cache.get("burston_prime_blueprint") == stats


def test_service_survives_missing_trade_statistics(tmp_path):
    client = FakeClient([order(p) for p in BURSTON_ASKS], stats_error=ConnectionError("v1 gone"))
    stats = PriceService(client, JsonPriceCache(tmp_path / "p.json")).get_price("burston_prime_blueprint")
    assert stats.basis == "listings" and stats.price == 5.0


def test_service_falls_back_to_stale_cache_when_api_down(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    cache.put(make_stats(fetched_at=time.time() - 3600))
    stats = PriceService(FakeClient(error=ConnectionError("down")), cache).get_price("nekros_prime_blueprint")
    assert stats.price == 30.0 and stats.stale


def test_service_returns_none_when_api_down_and_no_cache(tmp_path):
    service = PriceService(FakeClient(error=ConnectionError("down")), JsonPriceCache(tmp_path / "p.json"))
    assert service.get_price("nekros_prime_blueprint") is None


def test_cached_ignores_age_and_never_fetches(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    cache.put(make_stats(price=30.0, fetched_at=time.time() - 86400))  # a day old
    client = FakeClient(error=ConnectionError("must not be called"))
    service = PriceService(client, cache)
    assert service.cached("nekros_prime_blueprint").price == 30.0
    assert service.cached("unknown_item") is None
