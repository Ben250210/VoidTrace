"""Relic values: what a relic is worth on average, in plat and ducats, at each refinement.

Each relic holds 6 rewards: 3 common, 2 uncommon, 1 rare. Refining moves the odds toward the rare:

    per reward     common   uncommon   rare
    Intact         25.33%     11%       2%
    Exceptional    23.33%     13%       4%
    Flawless       20%        17%       6%
    Radiant        16.67%     20%      10%

Alone, a relic is worth the average of its rewards, weighted by those odds. In a squad, everyone
opens the same relic type and each player may pick any of the drops, so the value is the best of
4 independent rolls: E[max] = sum over values v of v x (P(max <= v) - P(max < v)), where
P(max <= v) = P(one roll <= v) ^ 4. Rewards and rarities come from voidtrace/data/relics.json
(tools/update_relics.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

DATA = Path(__file__).resolve().parent / "data" / "relics.json"
REFINEMENTS = ("intact", "exceptional", "flawless", "radiant")
ERAS = ("Lith", "Meso", "Neo", "Axi")
CHANCE = {
    "intact": {"common": 0.2533, "uncommon": 0.11, "rare": 0.02},
    "exceptional": {"common": 0.2333, "uncommon": 0.13, "rare": 0.04},
    "flawless": {"common": 0.20, "uncommon": 0.17, "rare": 0.06},
    "radiant": {"common": 0.1667, "uncommon": 0.20, "rare": 0.10},
}


@dataclass(frozen=True)
class Reward:
    name: str
    rarity: str  # common / uncommon / rare
    count: int = 1


@dataclass(frozen=True)
class Relic:
    name: str
    vaulted: Optional[bool]
    rewards: tuple[Reward, ...]

    @property
    def era(self) -> str:
        return self.name.split(" ", 1)[0]


def load_relics(path: Path = DATA) -> list[Relic]:
    """Every relic, Lith to Axi, then by name."""
    data = json.loads(path.read_text(encoding="utf-8"))["relics"]
    relics = [Relic(name, d.get("vaulted"), tuple(Reward(r["name"], r["rarity"], int(r.get("count", 1)))
                                                   for r in d["rewards"]))
              for name, d in data.items()]
    return sorted(relics, key=lambda r: (ERAS.index(r.era) if r.era in ERAS else 9, r.name))


def search_relics(query: str, relics: Sequence[Relic], limit: int = 30) -> list[Relic]:
    """"axi a1", "a1", "nikana" (a reward): relics whose name, or one of whose rewards, has every word."""
    words = query.casefold().replace("relic", "").split()
    if not words:
        return []
    hits = []
    for relic in relics:
        name = relic.name.casefold()
        rewards = " ".join(r.name.casefold() for r in relic.rewards)
        if all(w in name for w in words) or all(w in rewards for w in words):
            hits.append(relic)
    return hits[:limit]


def chance(rarity: str, refinement: str) -> float:
    return CHANCE[refinement].get(rarity, 0.0)


def expected_best(values_and_chances: Sequence[tuple[float, float]], picks: int = 1) -> float:
    """Expected value of the best of `picks` independent rolls of one relic."""
    ordered = sorted(values_and_chances)
    total = sum(p for _, p in ordered) or 1.0
    expected, below = 0.0, 0.0
    for value, p in ordered:
        at_or_below = below + p / total
        expected += value * (at_or_below ** picks - below ** picks)
        below = at_or_below
    return expected


def relic_value(relic: Relic, value_of: Callable[[Reward], Optional[float]], refinement: str,
                squad: int = 1) -> tuple[float, int]:
    """(average value, rewards with no value known yet). Unknown rewards count as 0 until known."""
    rows, missing = [], 0
    for reward in relic.rewards:
        value = value_of(reward)
        if value is None:
            missing += 1
            value = 0.0
        rows.append((value * reward.count, chance(reward.rarity, refinement)))
    return expected_best(rows, squad), missing
