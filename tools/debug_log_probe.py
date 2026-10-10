"""Measure how far EE.log lags behind Warframe's live debug output.

Listens to the debug-output channel and tails EE.log at the same time, and prints each Warframe line
with the wall-clock time it arrived from each source.

    python tools/debug_log_probe.py                 # run for 60 s
    python tools/debug_log_probe.py --seconds 300   # play a relic mission meanwhile
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voidtrace.debug_log import DebugChannelBusy, DebugLogSource  # noqa: E402
from voidtrace.log_watcher import LogTailer, default_log_path  # noqa: E402

INTERESTING = ("Got rewards", "Relic reward screen shut down", "gets reward", "Pause countdown done")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare debug-output and EE.log arrival times")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--all", action="store_true", help="print every line, not only reward-screen ones")
    args = parser.parse_args(argv)

    try:
        debug = DebugLogSource()
    except DebugChannelBusy as exc:
        print(f"debug channel busy: {exc}. Close DebugView/WFInfo and retry.")
        return 1
    tailer = LogTailer(default_log_path())
    tailer.poll()  # start at EOF

    file_seen: dict[str, float] = {}
    debug_seen: dict[str, float] = {}
    stop = threading.Event()

    def tail_file():
        while not stop.is_set():
            for line in tailer.poll():
                file_seen.setdefault(line.strip(), time.time())
            time.sleep(0.02)

    worker = threading.Thread(target=tail_file, daemon=True)
    worker.start()
    start = time.time()
    try:
        while time.time() - start < args.seconds:
            for line in debug.poll():
                key = line.strip()
                debug_seen.setdefault(key, time.time())
                if args.all or any(k in line for k in INTERESTING):
                    print(f"[debug +{time.time() - start:6.2f}s] {line[:140]}", flush=True)
    except KeyboardInterrupt:
        pass
    time.sleep(1.0)
    stop.set()
    debug.close()

    both = [k for k in debug_seen if k in file_seen]
    lags = sorted(file_seen[k] - debug_seen[k] for k in both)
    print(f"\n{debug.messages_seen} debug messages, {len(file_seen)} EE.log lines, {len(both)} matched")
    if lags:
        print(f"EE.log behind debug output: median {lags[len(lags) // 2]:.2f}s, max {lags[-1]:.2f}s")
        for key in both:
            if any(k in key for k in INTERESTING):
                print(f"  {file_seen[key] - debug_seen[key]:6.2f}s  {key[:100]}")
    elif debug.messages_seen == 0:
        print("No debug output from Warframe received. Is the game running?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
