"""Phase 2 CLI: crop reward names from a screenshot (or the live screen) and OCR them.

Examples:
    python 2_cv_cleaner.py --image shot.png                 # offline, auto-detect squad size
    python 2_cv_cleaner.py --image shot.png --players 4 --debug-dir debug/
    python 2_cv_cleaner.py --capture                        # grab the primary monitor now
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

from voidtrace.cv_cleaner import TesseractNotFound, capture_screen, read_reward_texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace Phase 2: reward-screen OCR")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="screenshot of the reward screen")
    source.add_argument("--capture", action="store_true", help="capture the screen now")
    parser.add_argument("--monitor", type=int, default=1, help="monitor index for --capture")
    parser.add_argument("--players", type=int, default=None, help="squad size 1-4 (default: detect)")
    parser.add_argument("--ui-scale", type=float, default=1.0, help="in-game UI scale (1.0 = 100%%)")
    parser.add_argument("--debug-dir", type=Path, default=None, help="save raw and cleaned crops here")
    args = parser.parse_args(argv)

    if args.image is not None:
        frame = cv2.imread(str(args.image))
        if frame is None:
            print(f"could not read image: {args.image}", file=sys.stderr)
            return 1
    else:
        frame = capture_screen(args.monitor)

    try:
        texts = read_reward_texts(frame, args.players, args.ui_scale, debug_dir=args.debug_dir)
    except TesseractNotFound as exc:
        print(exc, file=sys.stderr)
        return 2
    for i, text in enumerate(texts):
        print(f"slot {i}: {' '.join(text.split())!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
