import time

import pytest

from voidtrace.price_cleaner import (
    JsonPriceCache,
    PriceService,
    PriceStats,
    SupabasePriceCache,
    iqr_filter,
    select_sell_prices,
    summarize,
)


def order(plat, status="ingame", type_="sell", visible=True):
    return {"type": type_, "platinum": plat, "visible": visible, "user": {"status": status}}


# --- IQR ----------------------------------------------------------------------

def test_iqr_removes_lowball_and_gouge():
    kept, outliers = iqr_filter([13, 25, 27, 28, 30, 30, 30, 32, 35, 254])
    assert outliers == [13.0, 254.0]
    assert kept == [25, 27, 28, 30, 30, 30, 32, 35]


def test_iqr_leaves_tiny_samples_alone():
    kept, outliers = iqr_filter([1, 500, 3])
    assert outliers == []
    assert kept == [1, 3, 500]


def test_iqr_floor_keeps_neighbours_of_identical_cluster():
    # Q1 == Q3 == 30: without the floor, 28 and 32 would be thrown out.
    kept, outliers = iqr_filter([28, 30, 30, 30, 30, 30, 30, 32, 90])
    assert outliers == [90.0]
    assert 28.0 in kept and 32.0 in kept


# --- seller status tiers ------------------------------------------------------------

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


def test_summarize_reports_median_and_outlier_count():
    orders = [order(p) for p in (13, 25, 27, 28, 30, 30, 30, 32, 35, 254)]
    stats = summarize("nekros_prime_blueprint", orders, now=1000.0)
    assert stats.median == 30.0
    assert stats.n_orders == 10 and stats.n_kept == 8 and stats.n_outliers == 2
    assert (stats.low, stats.high) == (25.0, 35.0)
    assert stats.source == "ingame"


def test_summarize_with_no_sell_orders_returns_none():
    assert summarize("x", [order(10, type_="buy")]) is None


# --- caching / service ------------------------------------------------------------

def make_stats(slug="nekros_prime_blueprint", median=30.0, fetched_at=None):
    return PriceStats(slug, median, 28, 32, 25, 35, 10, 8, 2, "ingame", fetched_at or time.time())


def test_json_cache_roundtrip(tmp_path):
    cache = JsonPriceCache(tmp_path / "prices.json")
    assert cache.get("nekros_prime_blueprint") is None
    stats = make_stats()
    cache.put(stats)
    assert cache.get("nekros_prime_blueprint") == stats


class FakeClient:
    def __init__(self, orders=None, error=None):
        self.orders, self.error, self.calls = orders, error, []

    def get(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.orders


def test_service_uses_fresh_cache_without_network(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    cache.put(make_stats())
    client = FakeClient(error=AssertionError("should not be called"))
    assert PriceService(client, cache).get_price("nekros_prime_blueprint").median == 30.0
    assert client.calls == []


def test_service_fetches_live_and_caches(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    client = FakeClient([order(p) for p in (40, 41, 42, 43, 44)])
    stats = PriceService(client, cache).get_price("braton_prime_stock")
    assert client.calls == ["/orders/item/braton_prime_stock"]
    assert stats.median == 42.0 and not stats.stale
    assert cache.get("braton_prime_stock") == stats


def test_service_falls_back_to_stale_cache_when_api_down(tmp_path):
    cache = JsonPriceCache(tmp_path / "p.json")
    cache.put(make_stats(fetched_at=time.time() - 3600))
    stats = PriceService(FakeClient(error=ConnectionError("down")), cache).get_price("nekros_prime_blueprint")
    assert stats.median == 30.0 and stats.stale


def test_service_returns_none_when_api_down_and_no_cache(tmp_path):
    service = PriceService(FakeClient(error=ConnectionError("down")), JsonPriceCache(tmp_path / "p.json"))
    assert service.get_price("nekros_prime_blueprint") is None


# --- Supabase REST cache ----------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, rows):
        self.rows, self.posts, self.gets = rows, [], []

    def get(self, url, params, headers, timeout):
        self.gets.append((url, params, headers))
        return FakeResponse(self.rows)

    def post(self, url, json, headers, timeout):
        self.posts.append((url, json, headers))
        return FakeResponse()


def test_supabase_cache_queries_and_upserts():
    stats = make_stats(fetched_at=1000.0)
    row = {k: v for k, v in stats.__dict__.items() if k != "stale"}
    session = FakeSession([row])
    cache = SupabasePriceCache("https://abc.supabase.co/", "pk", session=session)

    assert cache.get("nekros_prime_blueprint") == stats
    url, params, headers = session.gets[0]
    assert url == "https://abc.supabase.co/rest/v1/price_cache"
    assert params["slug"] == "eq.nekros_prime_blueprint"
    assert headers["apikey"] == "pk"

    cache.put(stats)
    _, body, headers = session.posts[0]
    assert "stale" not in body and body["median"] == 30.0
    assert "merge-duplicates" in headers["Prefer"]
