"""Read-only tail of Warframe's EE.log that emits an event when the Void Fissure reward screen opens.

Nothing here writes to, locks, or injects into the game. The log is opened for reading only.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Keyword prefilter: cheap substring checks run before the regex.
# Same markers WFInfo (github.com/WFCD/WFinfo, Data.cs) uses to detect the relic
# reward screen. Not yet observed in this machine's EE.log; confirm with
# tools/sample_log_lines.py after a relic run.
KEYWORDS: tuple[str, ...] = ("Pause countdown done", "Got rewards")
TRIGGER_RE = re.compile(r"\b(?:Pause countdown done|Got rewards)\b")


def default_log_path() -> Path:
    """Return the default EE.log location: %LOCALAPPDATA%\\Warframe\\EE.log."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home() / "AppData" / "Local")
    return Path(local_appdata) / "Warframe" / "EE.log"


def is_reward_screen_line(line: str) -> bool:
    """True if a single log line indicates the reward-choice screen opened."""
    if not any(keyword in line for keyword in KEYWORDS):
        return False
    return TRIGGER_RE.search(line) is not None


class LogTailer:
    """Incrementally reads new complete lines from a log file.

    Handles: the file not existing yet, starting at EOF, partial trailing lines,
    and truncation or replacement (the game recreates EE.log on launch).
    """

    def __init__(self, path: Path | str, poll_interval: float = 0.05, start_at_eof: bool = True) -> None:
        self.path = Path(path)
        self.poll_interval = poll_interval
        self._start_at_eof = start_at_eof
        self._fh = None
        self._file_id: Optional[tuple[int, int]] = None
        self._offset = 0
        self._partial = ""
        self._first_open = True

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
        self._fh = None
        self._partial = ""

    def _stat_id(self) -> Optional[tuple[int, int]]:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def _open(self) -> bool:
        try:
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
        except OSError:
            self._fh = None
            return False
        self._file_id = self._stat_id()
        if self._first_open and self._start_at_eof:
            # Skip history so reward lines from earlier sessions never fire.
            self._fh.seek(0, os.SEEK_END)
        self._first_open = False
        self._offset = self._fh.tell()
        self._partial = ""
        return True

    def poll(self) -> list[str]:
        """Return complete lines appended since the last call. Never blocks."""
        if self._fh is None and not self._open():
            return []

        current_id = self._stat_id()
        if current_id is None:
            # File vanished (e.g. game restarting). Drop the handle and wait.
            self.close()
            return []

        try:
            size = os.fstat(self._fh.fileno()).st_size
        except OSError:
            self.close()
            return []

        replaced = self._file_id is not None and current_id != self._file_id
        truncated = size < self._offset
        if replaced or truncated:
            log.info("EE.log was replaced or truncated; reopening from the start")
            self.close()
            self._first_open = False  # new file: read it from the beginning
            if not self._open():
                return []

        chunk = self._fh.read()
        self._offset = self._fh.tell()
        if not chunk:
            return []

        data = self._partial + chunk
        pieces = data.split("\n")
        self._partial = pieces.pop()  # last piece is incomplete until a newline arrives
        return [piece.rstrip("\r") for piece in pieces]

    def lines(self, stop_event: Optional[threading.Event] = None):
        """Blocking generator of new lines. Stops when stop_event is set."""
        while stop_event is None or not stop_event.is_set():
            batch = self.poll()
            if batch:
                yield from batch
            else:
                time.sleep(self.poll_interval)


class DebouncedFilter:
    """Accepts reward-screen lines, suppressing repeats within `debounce` seconds.

    The window is measured from the last accepted event, so a suppressed
    duplicate does not extend the suppression window.
    """

    def __init__(self, debounce: float = 5.0) -> None:
        self.debounce = debounce
        self._last_accepted: Optional[float] = None

    def accept(self, line: str, now: float) -> bool:
        if not is_reward_screen_line(line):
            return False
        if self._last_accepted is not None and now - self._last_accepted < self.debounce:
            return False
        self._last_accepted = now
        return True


@dataclass(frozen=True)
class RewardScreenEvent:
    timestamp: datetime
    raw_line: str


class LogWatcher:
    """Runs a LogTailer on a daemon thread and calls `on_event` for each accepted match."""

    def __init__(
        self,
        path: Path | str,
        on_event: Callable[[RewardScreenEvent], None],
        debounce: float = 5.0,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tailer = LogTailer(path, poll_interval=poll_interval, start_at_eof=True)
        self._on_event = on_event
        self._filter = DebouncedFilter(debounce)
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lines_seen = 0
        self.events_emitted = 0

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("LogWatcher already started")
        self._thread = threading.Thread(target=self._run, name="VoidTrace-LogWatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._tailer.close()

    def _run(self) -> None:
        for line in self._tailer.lines(self._stop):
            self.lines_seen += 1
            if not self._filter.accept(line, self._clock()):
                continue
            self.events_emitted += 1
            event = RewardScreenEvent(timestamp=datetime.now(), raw_line=line)
            try:
                self._on_event(event)
            except Exception:
                # A bad consumer must not kill the tailing thread.
                log.exception("on_event callback raised")
