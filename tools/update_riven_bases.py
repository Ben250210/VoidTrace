"""Refresh voidtrace/data/riven_bases.json from Warframe's official Public Export.

    python tools/update_riven_bases.py

Every riven stat has a base value per riven type; a riven's value is
    base x 10 x (rank + 1) x weapon disposition x buff-count multiplier x roll (0.9-1.1)
VoidTrace uses that to grade rolls and to sanity-check what it reads off the screen. Run this after
a game update that changes riven stats. The export index is an LZMA ("alone") file whose size field
the standard decoder rejects, so it is set to "unknown" before decoding.
"""

from __future__ import annotations

import json
import lzma
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "voidtrace" / "data" / "riven_bases.json"
INDEX = "https://origin.warframe.com/PublicExport/index_en.txt.lzma"
MANIFEST = "http://content.warframe.com/PublicExport/Manifest/"

# Export entry -> Warframe.Market riven type (weapon.rivenType, or group for archguns).
TYPES = {
    "LotusRifleRandomModRare": "rifle",
    "LotusShotgunRandomModRare": "shotgun",
    "LotusPistolRandomModRare": "pistol",
    "PlayerMeleeWeaponRandomModRare": "melee",
    "LotusModularMeleeRandomModRare": "zaw",
    "LotusModularPistolRandomModRare": "kitgun",
    "LotusArchgunRandomModRare": "archgun",
}


def read_index() -> dict[str, str]:
    data = bytearray(requests.get(INDEX, timeout=30).content)
    data[5:13] = b"\xff" * 8  # "size unknown": the shipped size field makes lzma reject the file
    text = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(bytes(data)).decode()
    return {line.split("!")[0]: line.strip() for line in text.splitlines() if "!" in line}


def label(loc_tag: str) -> str:
    """"|val|% <DT_FIRE_COLOR>Heat" -> "Heat"; "|val|% Fire Rate (x2 for Bows)" -> "Fire Rate (x2 for Bows)"."""
    return re.sub(r"<[^>]+>", "", loc_tag.replace("|val|", "")).strip(" %x")


def main() -> int:
    index = read_index()
    upgrades = json.loads(requests.get(MANIFEST + index["ExportUpgrades_en.json"], timeout=60).text,
                          strict=False)["ExportUpgrades"]
    out: dict[str, dict] = {}
    for entry in upgrades:
        kind = TYPES.get(entry.get("uniqueName", "").rsplit("/", 1)[-1])
        if kind is None:
            continue
        stats = {}
        for e in entry.get("upgradeEntries", ()):
            value = e["upgradeValues"][0]
            stats[e["tag"]] = {"base": value["value"], "label": label(value.get("locTag", "")),
                               "prefix": e.get("prefixTag", ""), "suffix": e.get("suffixTag", "")}
        out[kind] = stats
    missing = set(TYPES.values()) - set(out)
    if missing:
        print(f"export has no riven data for: {sorted(missing)}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": index["ExportUpgrades_en.json"], "types": out}, indent=1, sort_keys=True),
                   encoding="utf-8")
    print(f"wrote {OUT} ({sum(len(v) for v in out.values())} stats over {len(out)} riven types)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
