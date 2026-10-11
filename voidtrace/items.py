"""Item data from Warframe.Market, fetched once and cached on disk.

Two views of the same `/v2/items` list:
  * load_items()   - possible relic rewards (prime parts + untradable Forma), for OCR matching
  * load_catalog() - every tradable item, mods included, for the market window's search
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
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
    max_rank: Optional[int] = None  # None for unranked items (prime parts); mods/arcanes have one


@dataclass(frozen=True)
class CatalogItem:
    name: str
    slug: str
    max_rank: Optional[int]
    tags: tuple[str, ...]
    id: str = ""  # Warframe.Market item id, needed to post orders


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VoidTrace"


def is_relic_reward(entry: dict) -> bool:
    """Prime parts and prime blueprints; excludes sets (never a single relic drop)."""
    tags = set(entry.get("tags", ()))
    return "prime" in tags and "set" not in tags and bool(tags & {"component", "blueprint"})


def compact(entry: dict) -> dict:
    """The fields VoidTrace uses from one /v2/items entry (raw API or already compact)."""
    if "name" in entry:
        return entry
    return {
        "name": entry["i18n"]["en"]["name"],
        "id": entry.get("id", ""),
        "slug": entry["slug"],
        "ducats": int(entry.get("ducats") or 0),
        "tags": list(entry.get("tags", ())),
        "max_rank": entry.get("maxRank"),
    }


def parse_items(data: list[dict]) -> list[Item]:
    items = [
        Item(name=e["name"], slug=e["slug"], ducats=e["ducats"], max_rank=e.get("max_rank"))
        for e in map(compact, data)
        if is_relic_reward(e)
    ]
    items.extend(Item(name=name, slug=None, ducats=0) for name in UNTRADABLE_REWARDS)
    return sorted(items, key=lambda item: item.name)


def parse_catalog(data: list[dict]) -> list[CatalogItem]:
    return sorted(
        (CatalogItem(e["name"], e["slug"], e.get("max_rank"), tuple(e.get("tags", ())), e.get("id", ""))
         for e in map(compact, data)),
        key=lambda item: item.name,
    )


def load_raw(
    client: Optional[MarketClient] = None,
    cache_path: Optional[Path] = None,
    max_age: float = CACHE_TTL,
) -> list[dict]:
    """The compact item list: fresh cache, else network, else stale cache."""
    cache_path = cache_path or default_cache_dir() / "catalog.json"
    cached = _read_cache(cache_path)
    if cached is not None and time.time() - cached[0] < max_age:
        return cached[1]
    try:
        data = [compact(entry) for entry in (client or MarketClient()).get("/items")]
    except Exception as exc:
        if cached is not None:
            log.warning("item list fetch failed (%s); using stale cache", exc)
            return cached[1]
        raise
    _write_cache(cache_path, data)
    return data


def load_items(client: Optional[MarketClient] = None, cache_path: Optional[Path] = None,
               max_age: float = CACHE_TTL) -> list[Item]:
    return parse_items(load_raw(client, cache_path, max_age))


def load_catalog(client: Optional[MarketClient] = None, cache_path: Optional[Path] = None,
                 max_age: float = CACHE_TTL) -> list[CatalogItem]:
    return parse_catalog(load_raw(client, cache_path, max_age))


def _read_cache(path: Path) -> Optional[tuple[float, list[dict]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw["items"]
        if items and ("tags" not in items[0] or "id" not in items[0]):
            return None  # written by an older version (no tags, or no item ids for posting orders)
        return raw["fetched_at"], items
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _write_cache(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "items": items}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
