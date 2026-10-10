"""Phase 3 CLI: clean raw OCR strings and resolve them to item names.

Examples:
    python 3_text_cleaner.py "N3kr0s Pr1m3" "AKSTlLETTO PRlME LINK"
    python 3_text_cleaner.py --image shot.png      # Phase 2 OCR feeding Phase 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from voidtrace.items import load_items
from voidtrace.text_cleaner import EntityResolver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace Phase 3: OCR text -> item names")
    parser.add_argument("texts", nargs="*", help="raw OCR strings")
    parser.add_argument("--image", type=Path, help="run Phase 2 OCR on a screenshot first")
    parser.add_argument("--cutoff", type=float, default=75.0, help="minimum match score 0-100")
    args = parser.parse_args(argv)

    texts = list(args.texts)
    if args.image is not None:
        import cv2

        from voidtrace.cv_cleaner import read_reward_texts

        texts += read_reward_texts(cv2.imread(str(args.image)))
    if not texts:
        parser.error("give OCR strings or --image")

    resolver = EntityResolver(load_items(), cutoff=args.cutoff)
    for res in resolver.resolve_all(texts):
        name = res.item.name if res.item else "<no match>"
        flag = "  (ambiguous)" if res.ambiguous else ""
        print(f"{' '.join(res.raw.split())!r:40} -> {name} [{res.score:.0f}]{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
