"""Refresh voidtrace/data/relics.json: every Lith / Meso / Neo / Axi relic and its 6 rewards.

    python tools/update_relics.py

Rewards and rarities come from Warframe's official Public Export (ExportRelicArcane). The export
names rewards by internal path, so names are looked up in the other exports: parts by their own name
(ExportResources: "Braton Prime Stock"), blueprints by what they build + " Blueprint" ("Nikana Prime
Blueprint"). Whether a relic is vaulted comes from Warframe.Market's item list. Run this after a
Prime Access or a Prime Resurgence (vaulting changes then).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.update_riven_bases import MANIFEST, read_index  # noqa: E402

OUT = ROOT / "voidtrace" / "data" / "relics.json"
ERAS = ("Lith", "Meso", "Neo", "Axi")
NAMED = ("ExportResources_en.json", "ExportWeapons_en.json", "ExportWarframes_en.json", "ExportSentinels_en.json")


def export(index: dict[str, str], key: str) -> list[dict]:
    data = json.loads(requests.get(MANIFEST + index[key], timeout=60).text, strict=False)
    return next(iter(data.values()))


def reward_namer(index: dict[str, str]):
    names: dict[str, str] = {}
    for key in NAMED:
        for e in export(index, key):
            if e.get("name"):
                names[e["uniqueName"]] = re.sub(r"<[^>]+>\s*", "", e["name"]).strip()  # "<ARCHWING> Odonata Prime"
    builds = {e["uniqueName"]: e.get("resultType", "") for e in export(index, "ExportRecipes_en.json")}

    def name(reward_path: str) -> str:
        path = reward_path.replace("/StoreItems", "")
        if path in names:
            return names[path]
        result = builds.get(path)
        if result and result in names:
            return names[result] + " Blueprint"
        return ""

    return name


def main() -> int:
    index = read_index()
    name_of = reward_namer(index)
    vaulted = {e["i18n"]["en"]["name"]: bool(e.get("vaulted"))
               for e in requests.get("https://api.warframe.market/v2/items", timeout=60).json()["data"]
               if "relic" in e.get("tags", ())}
    relics: dict[str, dict] = {}
    unnamed: set[str] = set()
    for e in export(index, "ExportRelicArcane_en.json"):
        title = e.get("name", "")
        rewards = e.get("relicRewards") or []
        if not title.startswith(ERAS) or len(rewards) != 6 or title in relics:
            continue  # refinement copies share the base relic's rewards
        out = []
        for r in rewards:
            reward = name_of(r["rewardName"])
            if not reward:
                unnamed.add(r["rewardName"])
                reward = r["rewardName"].rsplit("/", 1)[-1]
            out.append({"name": reward, "rarity": r["rarity"].lower(), "count": int(r.get("itemCount", 1))})
        relics[title] = {"vaulted": vaulted.get(title), "rewards": out}
    if unnamed:
        print(f"{len(unnamed)} reward(s) without a name, kept by path: {sorted(unnamed)[:5]}", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": index["ExportRelicArcane_en.json"], "relics": relics}, indent=1,
                              sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({len(relics)} relics, {sum(1 for r in relics.values() if r['vaulted'] is False)} unvaulted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
