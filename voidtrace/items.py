"""Master list of possible relic rewards, built from Warframe.Market and cached on disk."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .market import MarketClient

log = logging.getLogger(__name__)

CACHE_TTL = 24 * 3600  # seconds

# Relic rewards that are not tradable and so are absent from Warframe.Market.
UNTRADABLE_REWARDS = ("Forma Blueprint",)


@dataclass(frozen=True)
class Item:
    name: str
    slug: Optional[str]  # None for untradable rewards
    ducats: int


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VoidTrace"


def is_relic_reward(entry: dict) -> bool:
    """Prime parts and prime blueprints; excludes sets (never a single relic drop)."""
    tags = set(entry.get("tags", ()))
    return "prime" in tags and "set" not in tags and bool(tags & {"component", "blueprint"})


def parse_items(data: list[dict]) -> list[Item]:
    items = [
        Item(name=entry["i18n"]["en"]["name"], slug=entry["slug"], ducats=int(entry.get("ducats") or 0))
        for entry in data
        if is_relic_reward(entry)
    ]
    items.extend(Item(name=name, slug=None, ducats=0) for name in UNTRADABLE_REWARDS)
    return sorted(items, key=lambda item: item.name)


def load_items(
    client: Optional[MarketClient] = None,
    cache_path: Optional[Path] = None,
    max_age: float = CACHE_TTL,
) -> list[Item]:
    """Return the master item list: fresh cache, else network, else stale cache."""
    cache_path = cache_path or default_cache_dir() / "items.json"
    cached = _read_cache(cache_path)
    if cached is not None and time.time() - cached[0] < max_age:
        return cached[1]
    try:
        items = parse_items((client or MarketClient()).get("/items"))
    except Exception as exc:
        if cached is not None:
            log.warning("item list fetch failed (%s); using stale cache", exc)
            return cached[1]
        raise
    _write_cache(cache_path, items)
    return items


def _read_cache(path: Path) -> Optional[tuple[float, list[Item]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw["fetched_at"], [Item(**entry) for entry in raw["items"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, items: list[Item]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "items": [asdict(item) for item in items]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
