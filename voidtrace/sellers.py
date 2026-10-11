"""Seller lists and click-to-copy whispers, like warframe.market's order table.

Only reads public data. The whisper is text for the player to paste into game chat themselves;
nothing is sent to the game or to anyone's account.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from .market import MarketClient
from .price_cleaner import iqr_filter

CACHE_S = 60.0
STATUS_ORDER = {"ingame": 0, "online": 1}


@dataclass(frozen=True)
class SellerOrder:
    ingame_name: str
    status: str            # ingame / online
    platinum: int
    quantity: int
    rank: Optional[int]
    reputation: int
    updated_at: str
    suspicious: bool = False  # price is a statistical outlier (troll lowball or gouge)


def whisper_text(seller: SellerOrder, item_name: str, ranked: bool = False) -> str:
    """The message warframe.market copies for a buy, e.g.
    /w Kosmo Hi! I want to buy: "Primed Continuity (rank 10)" for 45 platinum. (warframe.market)
    """
    label = f"{item_name} (rank {seller.rank})" if ranked and seller.rank is not None else item_name
    return f'/w {seller.ingame_name} Hi! I want to buy: "{label}" for {seller.platinum} platinum. (warframe.market)'


def parse_sellers(orders: Sequence[dict], rank: Optional[int] = None, include_online: bool = True) -> list[SellerOrder]:
    """Visible sell orders from sellers who can trade now, cheapest first; outliers flagged, not hidden."""
    allowed = {"ingame", "online"} if include_online else {"ingame"}
    rows = []
    for o in orders:
        user = o.get("user") or {}
        if o.get("type") != "sell" or not o.get("visible", True) or user.get("status") not in allowed:
            continue
        if rank is not None and o.get("rank") != rank:
            continue
        rows.append(SellerOrder(
            ingame_name=user.get("ingameName", "?"),
            status=user["status"],
            platinum=int(o["platinum"]),
            quantity=int(o.get("quantity") or 1),
            rank=o.get("rank"),
            reputation=int(user.get("reputation") or 0),
            updated_at=o.get("updatedAt", ""),
        ))
    # Outliers are judged within each rank: a rank-10 mod at 100p is normal next to rank-0 copies at 45p.
    flagged: dict = {}
    for rank_value in {r.rank for r in rows}:
        _, outliers = iqr_filter([r.platinum for r in rows if r.rank == rank_value])
        flagged[rank_value] = list(outliers)
    marked = []
    for r in rows:
        if r.platinum in flagged[r.rank]:
            flagged[r.rank].remove(r.platinum)
            r = SellerOrder(**{**r.__dict__, "suspicious": True})
        marked.append(r)
    return sorted(marked, key=lambda r: (r.suspicious, STATUS_ORDER[r.status], r.platinum, -r.reputation))


def rank_counts(rows: Sequence[SellerOrder]) -> dict:
    """Sellers per rank, for the rank picker's labels."""
    counts: dict = {}
    for r in rows:
        counts[r.rank] = counts.get(r.rank, 0) + 1
    return counts


class SellerService:
    """Fetches seller lists through the shared rate-limited client, cached briefly per item."""

    def __init__(self, client: Optional[MarketClient] = None, cache_s: float = CACHE_S) -> None:
        self.client = client or MarketClient()
        self.cache_s = cache_s
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def orders(self, slug: str) -> list[dict]:
        with self._lock:
            hit = self._cache.get(slug)
            if hit and time.monotonic() - hit[0] < self.cache_s:
                return hit[1]
        data = self.client.get(f"/orders/item/{slug}")
        with self._lock:
            self._cache[slug] = (time.monotonic(), data)
        return data

    def sellers(self, slug: str, rank: Optional[int] = None, include_online: bool = True) -> list[SellerOrder]:
        return parse_sellers(self.orders(slug), rank, include_online)
