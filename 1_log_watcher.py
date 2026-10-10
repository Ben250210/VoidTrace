"""Phase 1 CLI: watch EE.log and print a line the moment the reward screen opens.

Examples:
    python 1_log_watcher.py                       # live tail of the default EE.log
    python 1_log_watcher.py --path other.log      # live tail of a custom file
    python 1_log_watcher.py --replay sample.log   # process a saved file, no debounce
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from voidtrace.log_watcher import (
    DebouncedFilter,
    LogWatcher,
    RewardScreenEvent,
    default_log_path,
)


def _print_event(event: RewardScreenEvent) -> None:
    stamp = event.timestamp.strftime("%H:%M:%S.") + f"{event.timestamp.microsecond // 1000:03d}"
    label = "REWARD SCREEN" if event.kind == "open" else "reward screen closed"
    print(f"[{stamp}] {label}", flush=True)


def run_replay(path: Path) -> int:
    """Process a file from the top. Debounce is disabled so every match is reported."""
    filt = DebouncedFilter(debounce=0.0)
    lines_seen = 0
    events = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            lines_seen += 1
            if filt.accept(line.rstrip("\r\n"), now=float(lines_seen)):
                events += 1
                print(f"line {lines_seen}: REWARD SCREEN")
    print(f"done: {lines_seen} lines, {events} events")
    return 0


def run_live(path: Path, debounce: float) -> int:
    print(f"watching {path} (Ctrl+C to stop)", flush=True)
    watcher = LogWatcher(path, on_event=_print_event, debounce=debounce)
    watcher.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        print(f"stopped: {watcher.lines_seen} lines read, {watcher.events_emitted} events")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace Phase 1: EE.log reward-screen watcher")
    parser.add_argument("--path", type=Path, default=None, help="EE.log path (default: %%LOCALAPPDATA%%\\Warframe\\EE.log)")
    parser.add_argument("--replay", type=Path, default=None, help="process a saved log file instead of tailing")
    parser.add_argument("--debounce", type=float, default=5.0, help="seconds to suppress repeat events (live mode)")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    if args.replay is not None:
        return run_replay(args.replay)
    return run_live(args.path or default_log_path(), args.debounce)


if __name__ == "__main__":
    sys.exit(main())
