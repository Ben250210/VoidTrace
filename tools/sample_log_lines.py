"""Discovery helper: print EE.log lines matching a pattern, so the real trigger string can be found.

Run this during or right after a relic reward screen appears, then compare the output against
KEYWORDS / TRIGGER_RE in voidtrace/log_watcher.py.

    python tools/sample_log_lines.py                      # default pattern, default EE.log
    python tools/sample_log_lines.py --pattern "Choice"   # custom regex (case-insensitive)
    python tools/sample_log_lines.py --tail 40            # only the last 40 matches
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running from anywhere: make the project root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voidtrace.log_watcher import default_log_path  # noqa: E402

DEFAULT_PATTERN = r"choice|reward|projection|fissure|relic"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find candidate reward-screen lines in EE.log")
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="case-insensitive regex")
    parser.add_argument("--tail", type=int, default=0, help="show only the last N matches (0 = all)")
    args = parser.parse_args(argv)

    path = args.path or default_log_path()
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    regex = re.compile(args.pattern, re.IGNORECASE)
    matches: list[tuple[int, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh, start=1):
            if regex.search(line):
                matches.append((number, line.rstrip("\r\n")))

    if args.tail > 0:
        matches = matches[-args.tail:]
    for number, text in matches:
        print(f"{number:>8}: {text}")
    print(f"-- {len(matches)} matching lines in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
