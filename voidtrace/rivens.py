"""Riven mods: weapon and stat lists, Warframe.Market riven auctions, and price checks.

Rivens are sold as auctions (one listing per unique riven), not as orders, so they have their own
endpoints: weapons and stats from v2 (/riven/weapons, /riven/attributes), auction search from v1
(/auctions/search?type=riven). Only public data is read here; selling lives in voidtrace.account.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlencode

import numpy as np

from .items import default_cache_dir
from .market import MarketClient
from .price_cleaner import iqr_filter

log = logging.getLogger(__name__)

CACHE_TTL = 24 * 3600   # weapon and stat lists
SEARCH_CACHE_S = 60.0   # auction searches
STATUS_ORDER = {"ingame": 0, "online": 1, "offline": 2}
MIN_COMPARABLES = 4     # prices needed before a price-check tier is trusted
CHEAPEST_K = 5          # the estimate is the median of this many cheapest kept prices


@dataclass(frozen=True)
class RivenWeapon:
    slug: str
    name: str
    group: str          # primary / secondary / melee / archgun / zaw / kitgun / sentinel
    riven_type: str     # rifle / pistol / shotgun / melee / zaw / kitgun
    disposition: float  # riven strength multiplier, about 0.5-1.55
    mr: int


@dataclass(frozen=True)
class RivenStat:
    slug: str
    name: str
    unit: Optional[str]                 # percent / multiply / seconds / None
    exclusive_to: Optional[tuple[str, ...]]
    positive_only: bool = False
    negative_only: bool = False
    positive_is_negative: bool = False  # e.g. recoil: a minus is the good side
    prefix: str = ""                    # name parts: "Crita" + "Can" -> "Critacan"
    suffix: str = ""

    def allowed_on(self, weapon: RivenWeapon) -> bool:
        return self.exclusive_to is None or bool({weapon.riven_type, weapon.group} & set(self.exclusive_to))


@dataclass(frozen=True)
class RivenAttr:
    slug: str
    value: float
    positive: bool  # a buff (True) or the curse (False)


@dataclass(frozen=True)
class RivenAuction:
    id: str
    weapon: str            # weapon slug
    name: str              # riven name, e.g. "concitron"
    attrs: tuple[RivenAttr, ...]
    mr: int
    rolls: int
    rank: int
    polarity: str
    buyout: Optional[int]  # None for bid-only auctions
    starting: int
    top_bid: Optional[int]
    direct: bool
    seller: str
    status: str            # ingame / online / offline
    reputation: int
    visible: bool = True
    minimal_reputation: int = 0
    note: str = ""

    @property
    def positives(self) -> frozenset[str]:
        return frozenset(a.slug for a in self.attrs if a.positive)

    @property
    def negative(self) -> Optional[str]:
        return next((a.slug for a in self.attrs if not a.positive), None)

    @property
    def price(self) -> Optional[int]:
        """What it costs now: the buyout, else the current top bid or starting price."""
        if self.buyout is not None:
            return self.buyout
        return self.top_bid if self.top_bid is not None else self.starting


POLARITIES = ("madurai", "vazarin", "naramon")


@dataclass(frozen=True)
class RivenDraft:
    """A riven you own, as typed in (or read off the screen): what gets price-checked and sold."""
    weapon: str = ""
    name: str = ""
    attrs: tuple[RivenAttr, ...] = ()
    mr: int = 8
    rolls: int = 0
    rank: int = 0
    polarity: str = "madurai"

    @property
    def positives(self) -> list[str]:
        return [a.slug for a in self.attrs if a.positive]

    @property
    def negative(self) -> Optional[str]:
        return next((a.slug for a in self.attrs if not a.positive), None)


@dataclass
class RivenCatalog:
    weapons: list[RivenWeapon]
    stats: list[RivenStat]
    by_weapon: dict[str, RivenWeapon] = field(init=False)
    by_stat: dict[str, RivenStat] = field(init=False)

    def __post_init__(self) -> None:
        self.by_weapon = {w.slug: w for w in self.weapons}
        self.by_stat = {s.slug: s for s in self.stats}

    def stats_for(self, weapon: RivenWeapon) -> list[RivenStat]:
        return [s for s in self.stats if s.allowed_on(weapon)]

    def stat_name(self, slug: str) -> str:
        stat = self.by_stat.get(slug)
        return stat.name if stat else slug.replace("_", " ").title()

    def riven_title(self, auction: RivenAuction) -> str:
        """"Ocucor Sati-toxitio", as the game shows it: only the first letter of the riven name is a
        capital (.title() would give "Sati-Toxitio")."""
        weapon = self.by_weapon.get(auction.weapon)
        weapon_name = weapon.name if weapon else auction.weapon.replace("_", " ").title()
        return f"{weapon_name} {auction.name[:1].upper()}{auction.name[1:]}".strip()

    def format_attr(self, attr: RivenAttr) -> str:
        """"+108.2% Critical Chance", "x1.08 Damage to Corpus", "-6% Zoom"."""
        stat = self.by_stat.get(attr.slug)
        unit = stat.unit if stat else "percent"
        name = self.stat_name(attr.slug)
        if unit == "multiply":
            return f"x{attr.value:g} {name}"
        sign = "+" if attr.value >= 0 else "-"
        suffix = {"percent": "%", "seconds": "s"}.get(unit or "", "")
        return f"{sign}{abs(attr.value):g}{suffix} {name}"


# --- weapon and stat lists ---------------------------------------------------------------------

def parse_weapons(data: Sequence[dict]) -> list[RivenWeapon]:
    return sorted(
        (RivenWeapon(slug=w["slug"], name=w["i18n"]["en"]["name"], group=w.get("group", ""),
                     riven_type=w.get("rivenType", ""), disposition=float(w.get("disposition") or 1.0),
                     mr=int(w.get("reqMasteryRank") or 0))
         for w in data),
        key=lambda w: w.name,
    )


def parse_stats(data: Sequence[dict]) -> list[RivenStat]:
    return sorted(
        (RivenStat(slug=a["slug"], name=a["i18n"]["en"]["name"], unit=a.get("unit"),
                   exclusive_to=tuple(a["exclusiveTo"]) if a.get("exclusiveTo") else None,
                   positive_only=bool(a.get("positiveOnly")), negative_only=bool(a.get("negativeOnly")),
                   positive_is_negative=bool(a.get("positiveIsNegative")),
                   prefix=a.get("prefix") or "", suffix=a.get("suffix") or "")
         for a in data),
        key=lambda s: s.name,
    )


def load_riven_catalog(client: Optional[MarketClient] = None, cache_path: Optional[Path] = None,
                       max_age: float = CACHE_TTL) -> RivenCatalog:
    """Weapons and stats: fresh cache, else network, else stale cache."""
    cache_path = cache_path or default_cache_dir() / "rivens.json"
    cached = _read_cache(cache_path)
    if cached is not None and time.time() - cached["fetched_at"] < max_age:
        raw = cached
    else:
        try:
            client = client or MarketClient()
            raw = {"fetched_at": time.time(), "weapons": client.get("/riven/weapons"),
                   "attributes": client.get("/riven/attributes")}
            _write_cache(cache_path, raw)
        except Exception as exc:
            if cached is None:
                raise
            log.warning("riven list fetch failed (%s); using stale cache", exc)
            raw = cached
    return RivenCatalog(parse_weapons(raw["weapons"]), parse_stats(raw["attributes"]))


def _read_cache(path: Path) -> Optional[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["weapons"] and raw["attributes"]:
            return raw
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_cache(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(path)


# --- auctions -------------------------------------------------------------------------------------

def parse_auction(a: dict, seller: Optional[str] = None) -> RivenAuction:
    """One v1 auction. Your own auctions come back with `owner` as a bare user id: pass `seller`."""
    item = a.get("item") or {}
    owner = a.get("owner") if isinstance(a.get("owner"), dict) else {}
    return RivenAuction(
        id=a["id"],
        weapon=item.get("weapon_url_name", ""),
        name=item.get("name", ""),
        attrs=tuple(RivenAttr(x["url_name"], float(x["value"]), bool(x["positive"]))
                    for x in item.get("attributes", ())),
        mr=int(item.get("mastery_level") or 0),
        rolls=int(item.get("re_rolls") or 0),
        rank=int(item.get("mod_rank") or 0),
        polarity=item.get("polarity", ""),
        buyout=a.get("buyout_price"),
        starting=int(a.get("starting_price") or 0),
        top_bid=a.get("top_bid"),
        direct=bool(a.get("is_direct_sell")),
        seller=seller or owner.get("ingame_name", "?"),
        status=owner.get("status", "offline"),
        reputation=int(owner.get("reputation") or 0),
        visible=bool(a.get("visible", True)),
        minimal_reputation=int(a.get("minimal_reputation") or 0),
        note=a.get("note_raw") or "",
    )


def buffs_from_name(catalog: "RivenCatalog", name: str,
                    weapon: Optional[RivenWeapon] = None) -> Optional[list[list[str]]]:
    """The buffs a riven name stands for, the reverse of valid_names(): "igni-insitron" is Heat
    (igni) + Puncture (insi) + Damage to Corpus (tron).

    Returns one list of candidate stats per buff, in name order, or None when the name fits no
    buffs. A buff has several candidates only where stats share a name part (Laci/Nus melee stats,
    Parci/Pia); `weapon` narrows them to stats it can roll.
    """
    stats = catalog.stats_for(weapon) if weapon else catalog.stats
    prefixes: dict[str, list[str]] = {}
    suffixes: dict[str, list[str]] = {}
    for s in stats:
        if s.prefix and s.suffix:
            prefixes.setdefault(s.prefix.lower(), []).append(s.slug)
            suffixes.setdefault(s.suffix.lower(), []).append(s.slug)
    name = name.strip().lower()

    def split(text: str) -> list[tuple[str, str]]:  # (prefix, suffix) pairs that make up `text`
        return [(p, text[len(p):]) for p in prefixes if text.startswith(p) and text[len(p):] in suffixes]

    parses = []
    if name.count("-") == 1:
        first, rest = name.split("-")
        if first in prefixes:
            parses = [[prefixes[first], prefixes[p], suffixes[s]] for p, s in split(rest)]
    elif "-" not in name:
        for p, s in split(name):
            shared = [slug for slug in prefixes[p] if slug in suffixes[s]]
            parses.append([shared] if shared else [prefixes[p], suffixes[s]])  # "critacron" is one buff
    # Different splits of one name (exi + ... vs exiti + ...) are rare; keep the one with the fewest
    # undecided buffs.
    parses = [p for p in parses if all(p)]
    if not parses:
        return None
    return min(parses, key=lambda p: (sum(len(c) > 1 for c in p), len(p)))


def valid_names(catalog: "RivenCatalog", positives: Sequence[str]) -> set[str]:
    """Riven names the game can give these buffs (Warframe.Market rejects any other name).

    Each buff has a prefix and a suffix: one buff is prefix+suffix ("critacron"), two are
    prefix+suffix ("critacan"), three are prefix-prefix+suffix ("sati-critacron"); the order follows
    the roll sizes, so every order is accepted. Matched 666 of 666 real Kulstar and Rubico listings.
    """
    parts = {s.slug: ((s.prefix or "").lower(), (s.suffix or "").lower()) for s in catalog.stats}
    if not positives or any(p not in parts for p in positives) or len(positives) > 3:
        return set()
    names = set()
    for order in itertools.permutations(positives):
        if len(order) == 1:
            names.add(parts[order[0]][0] + parts[order[0]][1])
        elif len(order) == 2:
            names.add(parts[order[0]][0] + parts[order[1]][1])
        else:
            names.add(f"{parts[order[0]][0]}-{parts[order[1]][0]}{parts[order[2]][1]}")
    return names


CLEAN, ROLLED = "clean", "rolled"


def search_path(weapon: str, positives: Sequence[str] = (), negative: Optional[str] = None,
                buyout_only: bool = False, rolls: Optional[str] = None) -> str:
    """v1 search path. `negative`: None = any, "none" = no curse, "has" = any curse, or a stat slug.
    `rolls`: None = any, "clean" = never rolled (0 rolls), "rolled" = 1 or more."""
    params = {"type": "riven", "weapon_url_name": weapon, "sort_by": "price_asc"}
    if positives:
        params["positive_stats"] = ",".join(positives)
    if negative:
        params["negative_stats"] = negative
    if buyout_only:
        params["buyout_policy"] = "direct"
    if rolls == CLEAN:
        params.update(re_rolls_min=0, re_rolls_max=0)
    elif rolls == ROLLED:
        params["re_rolls_min"] = 1
    return "/auctions/search?" + urlencode(params)


def sort_auctions(auctions: Sequence[RivenAuction]) -> list[RivenAuction]:
    """Sellers who can trade now first, then cheapest, then best reputation."""
    return sorted(auctions, key=lambda a: (STATUS_ORDER.get(a.status, 3), a.price is None, a.price or 0,
                                           -a.reputation))


WHISPER_TAG = "(warframe.market through VoidTrace)"


def riven_whisper(auction: RivenAuction, title: str) -> str:
    """Message for the player to paste in game chat. warframe.market itself copies only "/w Name Hi!"
    for rivens; this names the riven and price (like riven.market does), so the seller knows which
    one you mean."""
    if auction.price is None:
        return f"/w {auction.seller} Hi! I'm interested in your {title} riven. {WHISPER_TAG}"
    return f"/w {auction.seller} Hi! I'd like to buy your {title} riven for {auction.price} platinum. {WHISPER_TAG}"


class RivenService:
    """Riven auction searches through the shared rate-limited client, cached briefly."""

    def __init__(self, client: Optional[MarketClient] = None, cache_s: float = SEARCH_CACHE_S) -> None:
        self.client = client or MarketClient()
        self.cache_s = cache_s
        self._cache: dict[str, tuple[float, list[RivenAuction]]] = {}
        self._lock = threading.Lock()

    def search(self, weapon: str, positives: Sequence[str] = (), negative: Optional[str] = None,
               buyout_only: bool = False, statuses: Sequence[str] = ("ingame", "online", "offline"),
               rolls: Optional[str] = None) -> list[RivenAuction]:
        """`rolls` narrows the listings shown (clean / rolled). The price check never passes it:
        whether a riven was rolled is for finding and labelling, not for its estimate."""
        path = search_path(weapon, positives, negative, buyout_only, rolls)
        with self._lock:
            hit = self._cache.get(path)
        if hit and time.monotonic() - hit[0] < self.cache_s:
            auctions = hit[1]
        else:
            payload = self.client.get_v1(path)
            auctions = [parse_auction(a) for a in payload.get("auctions", ())
                        if not a.get("closed") and a.get("visible", True)]
            with self._lock:
                self._cache[path] = (time.monotonic(), auctions)
        return sort_auctions([a for a in auctions if a.status in statuses])


# --- price check ------------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceCheck:
    estimate: Optional[float]      # median of the cheapest kept prices; None if nothing comparable
    low: Optional[int]
    high: Optional[int]
    count: int                     # comparable listings used
    tier: str                      # how alike the comparables are
    comparables: tuple[RivenAuction, ...]
    outliers: tuple[int, ...] = ()
    exact: tuple[RivenAuction, ...] = ()  # same positive stats, whatever the curse: shown even when too few


def comparable_tiers(positives: Sequence[str], negative: Optional[str]) -> list[tuple[str, callable]]:
    """Ways a listing can be "like" this riven, most alike first."""
    pos = frozenset(positives)
    tiers = []
    if pos:
        tiers.append(("same stats and curse" if negative else "same stats, no curse",
                      lambda a: a.positives == pos and a.negative == negative))
        tiers.append(("same positive stats", lambda a: a.positives == pos))
        tiers.append(("your positive stats plus another", lambda a: a.positives > pos))
        if len(pos) > 1:
            tiers.append((f"{len(pos) - 1} of the same positive stats",
                          lambda a: len(a.positives & pos) >= len(pos) - 1))
        tiers.append(("one of the same positive stats", lambda a: bool(a.positives & pos)))
    tiers.append(("any riven for this weapon", lambda a: True))
    return tiers


def price_check(auctions: Sequence[RivenAuction], positives: Sequence[str], negative: Optional[str] = None,
                exclude_seller: Optional[str] = None, min_count: int = MIN_COMPARABLES) -> PriceCheck:
    """Estimate a riven's price from listings of the same weapon with the same stats.

    Riven prices are asking prices (rivens trade in person, there is no trade history), so the
    estimate is the median of the cheapest few after removing outliers: what you would have to beat
    to sell soon. Falls back to looser matches when too few listings are that alike.
    """
    me = (exclude_seller or "").casefold()
    pool = [a for a in auctions if a.price is not None and (not me or a.seller.casefold() != me)]
    chosen: list[RivenAuction] = []
    tier = "no listings"
    for label, matches in comparable_tiers(positives, negative):
        hits = [a for a in pool if matches(a)]
        if hits:
            chosen, tier = hits, label
        if len(hits) >= min_count:
            break
    pos = frozenset(positives)
    exact = tuple(sorted((a for a in pool if pos and a.positives == pos), key=lambda a: a.price))
    if not chosen:
        return PriceCheck(None, None, None, 0, tier, (), exact=exact)
    kept, outliers = iqr_filter([a.price for a in chosen])
    kept_ids = list(kept)
    comparables = []
    for a in sorted(chosen, key=lambda a: a.price):
        if a.price in kept_ids:
            kept_ids.remove(a.price)
            comparables.append(a)
    estimate = float(np.median(kept[:CHEAPEST_K])) if kept else None
    return PriceCheck(estimate, int(kept[0]) if kept else None, int(kept[-1]) if kept else None,
                      len(kept), tier, tuple(comparables), tuple(int(o) for o in outliers), exact)
