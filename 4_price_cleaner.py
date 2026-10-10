"""Phase 4 CLI: cleaned market price for items, by name (fuzzy) or slug.

Examples:
    python 4_price_cleaner.py "Nekros Prime Blueprint" "akstiletto prime barrel"
    python 4_price_cleaner.py --slug nekros_prime_blueprint
"""

from __future__ import annotations

import argparse
import sys

from voidtrace.items import load_items
from voidtrace.price_cleaner import PriceService, price_basis_label
from voidtrace.text_cleaner import EntityResolver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace Phase 4: cleaned Warframe.Market prices")
    parser.add_argument("names", nargs="*", help="item names (fuzzy matched)")
    parser.add_argument("--slug", action="append", default=[], help="exact Warframe.Market slug")
    args = parser.parse_args(argv)
    if not args.names and not args.slug:
        parser.error("give item names or --slug")

    targets: list[tuple[str, str | None, int | None]] = [(slug, slug, None) for slug in args.slug]
    if args.names:
        resolver = EntityResolver(load_items())
        for res in resolver.resolve_all(args.names):
            if res.item is None:
                print(f"{res.raw!r}: no matching item")
                continue
            targets.append((res.item.name, res.item.slug, res.item.ducats))

    service = PriceService()
    for label, slug, ducats in targets:
        if slug is None:
            print(f"{label}: untradable (0 plat)")
            continue
        stats = service.get_price(slug)
        if stats is None:
            print(f"{label}: price unavailable")
            continue
        ducat_text = f", {ducats} ducats" if ducats is not None else ""
        stale = " [STALE]" if stats.stale else ""
        print(f"{label}: {stats.price:g}p ({price_basis_label(stats)}){ducat_text}{stale}")
        if stats.sell_now is not None:
            print(
                f"    buy now {stats.sell_now:g}p from {stats.source} sellers: {stats.n_orders} asks, "
                f"{stats.n_outliers} outliers purged, kept {stats.low:g}-{stats.high:g}p"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
