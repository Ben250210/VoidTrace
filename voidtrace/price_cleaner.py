"""Phase 4: turn raw Warframe.Market orders into a trustworthy platinum price.

Cleaning steps:
  1. Keep visible sell orders only.
  2. Prefer sellers who are in game (can trade now); widen to online, then everyone, only if
     there are too few to be meaningful. The source tier is recorded with the result.
  3. Drop statistical outliers with the IQR rule (troll lowballs, price-gouging listings).
  4. Report the median of what remains.
"""

from __future__ import annotations

import json
import logging
import os
import time
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
MIN_SAMPLES = 5
IQR_K = 1.5
PRICE_TTL = 15 * 60  # seconds a cached price counts as fresh


@dataclass(frozen=True)
class PriceStats:
    slug: str
    median: float
    q1: float
    q3: float
    low: float          # cheapest order kept after cleaning
    high: float         # most expensive order kept after cleaning
    n_orders: int       # sell orders in the chosen status tier
    n_kept: int
    n_outliers: int
    source: str         # status tier used: ingame / online / all
    fetched_at: float
    stale: bool = False  # True when served from cache after a failed live fetch


def iqr_filter(values: Sequence[float], k: float = IQR_K) -> tuple[list[float], list[float]]:
    """Split values into (kept, outliers) using Tukey's fences [Q1 - k*IQR, Q3 + k*IQR].

    With fewer than 4 values there is no meaningful spread, so nothing is removed. The IQR is
    floored at 10% of the median (min 1 plat) so a cluster of identical prices does not turn
    every neighbouring price into an "outlier".
    """
    data = sorted(float(v) for v in values)
    if len(data) < 4:
        return data, []
    q1, q3 = np.percentile(data, [25, 75])
    spread = max(q3 - q1, 0.1 * float(np.median(data)), 1.0)
    lo, hi = q1 - k * spread, q3 + k * spread
    kept = [v for v in data if lo <= v <= hi]
    outliers = [v for v in data if v < lo or v > hi]
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


def summarize(slug: str, orders: Sequence[dict], now: Optional[float] = None) -> Optional[PriceStats]:
    prices, tier = select_sell_prices(orders)
    if not prices:
        return None
    kept, outliers = iqr_filter(prices)
    q1, q3 = np.percentile(kept, [25, 75])
    return PriceStats(
        slug=slug,
        median=float(np.median(kept)),
        q1=float(q1),
        q3=float(q3),
        low=kept[0],
        high=kept[-1],
        n_orders=len(prices),
        n_kept=len(kept),
        n_outliers=len(outliers),
        source=tier,
        fetched_at=time.time() if now is None else now,
    )


# --- caches -------------------------------------------------------------------

class PriceCache(Protocol):
    def get(self, slug: str) -> Optional[PriceStats]: ...
    def put(self, stats: PriceStats) -> None: ...


class JsonPriceCache:
    """Local file cache. Default; needs no setup."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or default_cache_dir() / "prices.json"

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, slug: str) -> Optional[PriceStats]:
        entry = self._load().get(slug)
        try:
            return PriceStats(**entry) if entry else None
        except TypeError:
            return None

    def put(self, stats: PriceStats) -> None:
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

    def get_price(self, slug: str) -> Optional[PriceStats]:
        cached = self._cached(slug)
        if cached is not None and time.time() - cached.fetched_at < self.max_age:
            return cached
        try:
            stats = summarize(slug, self.client.get(f"/orders/item/{slug}"))
        except Exception as exc:
            log.warning("live price fetch failed for %s: %s", slug, exc)
            return replace(cached, stale=True) if cached is not None else None
        if stats is not None:
            try:
                self.cache.put(stats)
            except Exception as exc:
                log.warning("price cache write failed for %s: %s", slug, exc)
        return stats
