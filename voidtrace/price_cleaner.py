"""Phase 4: turn raw Warframe.Market data into a platinum price you could actually trade at.

The headline price comes from completed trades when there are enough of them:
  * volume-weighted median of closed-trade medians over the last 48 hours,
  * else over the last 7 days.
Asking prices are a biased sample (cheap listings sell and disappear, overpriced ones linger),
so listings are only the fallback and the "buy now" reference:
  1. Keep visible sell orders.
  2. Prefer sellers who are in game; widen to online, then everyone, only if too few remain.
  3. Drop outliers with Tukey's IQR fences computed on log prices (prices spread
     multiplicatively, and a linear lower fence goes negative and never catches lowballs).
  4. "Sell now" = median of the cheapest few remaining asks: what a buyer would pay today.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Protocol, Sequence

import numpy as np
import requests

from .items import default_cache_dir
from .market import MarketClient

log = logging.getLogger(__name__)

STATUS_TIERS: tuple[tuple[str, frozenset[str]], ...] = (
    ("ingame", frozenset({"ingame"})),
    ("online", frozenset({"ingame", "online"})),
    ("all", frozenset({"ingame", "online", "offline"})),
)
MIN_SAMPLES = 5      # listings needed before trusting a seller tier
MIN_TRADES = 5       # closed trades needed before trusting trade history
SELL_NOW_K = 5       # cheapest asks used for the "sell now" price
IQR_K = 1.5
PRICE_TTL = 15 * 60  # seconds a cached price counts as fresh


@dataclass(frozen=True)
class PriceStats:
    slug: str
    price: float                  # headline price shown to the user
    basis: str                    # trades_48h / trades_7d / listings
    trade_volume: int             # closed trades behind the headline (0 if listings)
    sell_now: Optional[float]     # median of the cheapest kept asks in the chosen tier
    low: Optional[float]          # cheapest kept ask
    high: Optional[float]         # most expensive kept ask
    n_orders: int                 # sell orders in the chosen tier
    n_outliers: int
    source: str                   # seller tier used for listings: ingame / online / all / none
    fetched_at: float
    stale: bool = False           # served from cache after a failed live fetch


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    pairs = sorted(zip(values, weights))
    half = sum(w for _, w in pairs) / 2
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= half:
            return float(value)
    return float(pairs[-1][0])


def iqr_filter(values: Sequence[float], k: float = IQR_K) -> tuple[list[float], list[float]]:
    """Split prices into (kept, outliers) with Tukey's fences on log prices.

    With fewer than 4 values nothing is removed. The log-IQR is floored so a tight cluster
    does not flag its neighbours: at least 0.1 (about +/-10%) and at least one plat of
    spread around the median, which matters for cheap items where 2p vs 3p is normal.
    """
    data = sorted(float(v) for v in values if v > 0)
    if len(data) < 4:
        return data, []
    logs = np.log(data)
    q1, q3 = np.percentile(logs, [25, 75])
    median = float(np.median(data))
    spread = max(q3 - q1, 0.1, math.log1p(1.0 / median))
    lo, hi = q1 - k * spread, q3 + k * spread
    kept = [v for v, lv in zip(data, logs) if lo <= lv <= hi]
    outliers = [v for v, lv in zip(data, logs) if lv < lo or lv > hi]
    return kept, outliers


def select_sell_prices(orders: Sequence[dict], min_samples: int = MIN_SAMPLES) -> tuple[list[float], str]:
    """Visible sell-order prices from the narrowest seller-status tier with enough samples."""
    sells = [o for o in orders if o.get("type") == "sell" and o.get("visible", True)]
    prices: list[float] = []
    tier = STATUS_TIERS[-1][0]
    for tier, statuses in STATUS_TIERS:
        prices = [float(o["platinum"]) for o in sells if o.get("user", {}).get("status") in statuses]
        if len(prices) >= min_samples:
            break
    return prices, tier


def trade_price(statistics: Optional[dict], min_trades: int = MIN_TRADES) -> tuple[Optional[float], int, Optional[str]]:
    """(price, volume, basis) from Warframe.Market closed-trade statistics, or (None, 0, None)."""
    closed = (statistics or {}).get("statistics_closed") or {}
    windows = (("trades_48h", closed.get("48hours") or []), ("trades_7d", (closed.get("90days") or [])[-7:]))
    for basis, buckets in windows:
        buckets = [b for b in buckets if b.get("volume") and b.get("median") is not None]
        volume = sum(int(b["volume"]) for b in buckets)
        if volume >= min_trades:
            return weighted_median([b["median"] for b in buckets], [b["volume"] for b in buckets]), volume, basis
    return None, 0, None


def summarize(
    slug: str,
    orders: Sequence[dict],
    statistics: Optional[dict] = None,
    now: Optional[float] = None,
) -> Optional[PriceStats]:
    prices, tier = select_sell_prices(orders)
    kept, outliers = iqr_filter(prices)
    sell_now = float(np.median(kept[:SELL_NOW_K])) if kept else None
    traded, volume, basis = trade_price(statistics)
    if traded is None and sell_now is None:
        return None
    return PriceStats(
        slug=slug,
        price=traded if traded is not None else sell_now,
        basis=basis or "listings",
        trade_volume=volume,
        sell_now=sell_now,
        low=kept[0] if kept else None,
        high=kept[-1] if kept else None,
        n_orders=len(prices),
        n_outliers=len(outliers),
        source=tier if prices else "none",
        fetched_at=time.time() if now is None else now,
    )


def price_basis_label(price: PriceStats) -> str:
    """Where the headline price came from, in a few words."""
    if price.basis == "trades_48h":
        return f"{price.trade_volume} sold / 48h"
    if price.basis == "trades_7d":
        return f"{price.trade_volume} sold / 7d"
    return f"listings ({price.source})"


# --- caches -------------------------------------------------------------------

class PriceCache(Protocol):
    def get(self, slug: str) -> Optional[PriceStats]: ...
    def put(self, stats: PriceStats) -> None: ...


class JsonPriceCache:
    """Local file cache. Default; needs no setup."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_cache_dir() / "prices.json"
        self._lock = threading.Lock()  # prices are fetched in parallel threads

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, slug: str) -> Optional[PriceStats]:
        entry = self._load().get(slug)
        try:
            return PriceStats(**entry) if entry else None
        except TypeError:  # entry written by an older schema
            return None

    def put(self, stats: PriceStats) -> None:
        with self._lock:
            data = self._load()
            data[stats.slug] = asdict(replace(stats, stale=False))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.path)


class SupabasePriceCache:
    """Shared cache in a Supabase table via its REST API (schema: supabase/migrations/)."""

    COLUMNS = [f for f in PriceStats.__dataclass_fields__ if f != "stale"]

    def __init__(self, url: str, key: str, table: str = "price_cache", session: Optional[requests.Session] = None) -> None:
        self.endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.session = session or requests.Session()

    def get(self, slug: str) -> Optional[PriceStats]:
        resp = self.session.get(
            self.endpoint,
            params={"slug": f"eq.{slug}", "select": ",".join(self.COLUMNS)},
            headers=self.headers,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        return PriceStats(**rows[0]) if rows else None

    def put(self, stats: PriceStats) -> None:
        row = {k: v for k, v in asdict(stats).items() if k in self.COLUMNS}
        resp = self.session.post(
            self.endpoint,
            json=row,
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            timeout=10,
        )
        resp.raise_for_status()


def cache_from_env() -> PriceCache:
    """Supabase cache when SUPABASE_URL and SUPABASE_KEY are set, else the local JSON cache."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if url and key:
        return SupabasePriceCache(url, key)
    return JsonPriceCache()


# --- service ------------------------------------------------------------------

class PriceService:
    """Fresh cache -> live API -> stale cache, in that order."""

    def __init__(
        self,
        client: Optional[MarketClient] = None,
        cache: Optional[PriceCache] = None,
        max_age: float = PRICE_TTL,
    ) -> None:
        self.client = client or MarketClient()
        self.cache = cache if cache is not None else cache_from_env()
        self.max_age = max_age

    def _cached(self, slug: str) -> Optional[PriceStats]:
        try:
            return self.cache.get(slug)
        except Exception as exc:
            log.warning("price cache read failed for %s: %s", slug, exc)
            return None

    def _statistics(self, slug: str) -> Optional[dict]:
        # Trade history comes from the legacy v1 API; listings still work if it is gone.
        try:
            return self.client.get_v1(f"/items/{slug}/statistics")
        except Exception as exc:
            log.warning("trade statistics unavailable for %s: %s", slug, exc)
            return None

    def get_price(self, slug: str) -> Optional[PriceStats]:
        cached = self._cached(slug)
        if cached is not None and time.time() - cached.fetched_at < self.max_age:
            return cached
        # Orders and trade history in parallel: each request takes 0.5-1.2 s, and the client's
        # lock only spaces their start times to respect the rate limit.
        with ThreadPoolExecutor(max_workers=2) as pool:
            orders_future = pool.submit(self.client.get, f"/orders/item/{slug}")
            stats_future = pool.submit(self._statistics, slug)
            try:
                orders = orders_future.result()
            except Exception as exc:
                log.warning("live price fetch failed for %s: %s", slug, exc)
                return replace(cached, stale=True) if cached is not None else None
            statistics = stats_future.result()
        stats = summarize(slug, orders, statistics)
        if stats is not None:
            try:
                self.cache.put(stats)
            except Exception as exc:
                log.warning("price cache write failed for %s: %s", slug, exc)
        return stats
