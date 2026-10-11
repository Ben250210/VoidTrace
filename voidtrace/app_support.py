"""Things the packaged app needs that a developer run doesn't: a log file, one running copy,
and a self-test a player can run (and send back) when something does not work."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Callable, Optional

from .items import default_cache_dir

log = logging.getLogger("voidtrace")


def log_path() -> Path:
    return default_cache_dir() / "voidtrace.log"


def setup_logging(verbose: bool = False) -> Path:
    """Log to %LOCALAPPDATA%\\VoidTrace\\voidtrace.log (rotated), and to the console when there is one.
    The packaged app has no console, so the file is where errors end up."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for noisy in ("urllib3", "keyring"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def log_crash(exc_type, exc, tb):
        log.critical("VoidTrace crashed", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = log_crash
    return path


def acquire_single_instance():
    """Return a held lock, or None if VoidTrace is already running (two copies would fight over
    the hotkey and the game's debug-output channel)."""
    from PyQt6.QtCore import QLockFile

    path = default_cache_dir() / "voidtrace.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)  # a lock left by a crashed copy is detected by its dead process id
    return lock if lock.tryLock(200) else None


# --- self-test --------------------------------------------------------------------------------

Check = tuple[str, bool, str]


def _check(name: str, fn: Callable[[], str]) -> Check:
    try:
        return name, True, fn()
    except Exception as exc:  # each check reports, none stops the others
        return name, False, str(exc) or exc.__class__.__name__


def self_test(game_log_status: Optional[str] = None) -> list[Check]:
    """Quick checks of everything VoidTrace needs, in plain words. A running VoidTrace passes
    `game_log_status` (it already owns the game-log channel, so probing it would report "busy")."""
    import cv2
    import numpy as np

    from . import cv_cleaner
    from .items import load_items
    from .text_cleaner import EntityResolver

    results: list[Check] = []
    items: list = []

    def tesseract() -> str:
        path = cv_cleaner.find_tesseract()
        if path is None:
            raise RuntimeError("Tesseract OCR not found")
        return path

    def market() -> str:
        items.extend(load_items())
        return f"{len(items)} relic rewards loaded from Warframe.Market"

    def ocr() -> str:
        img = np.full((60, 420, 3), 25, np.uint8)
        cv2.putText(img, "FORMA BLUEPRINT", (12, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (230, 230, 230), 2, cv2.LINE_AA)
        text = " ".join(cv_cleaner.ocr(cv_cleaner.preprocess(img)).split())
        match = EntityResolver(items).resolve(text).item if items else None
        if match is None or match.name != "Forma Blueprint":
            raise RuntimeError(f"read {text!r}")
        return f"read {text!r}"

    def game_log() -> str:
        from .debug_log import DebugChannelBusy, DebugLogSource, find_warframe_pid

        if game_log_status is not None:
            if game_log_status.startswith("FAIL:"):
                raise RuntimeError(game_log_status[5:].strip())
            return game_log_status

        try:
            DebugLogSource().close()
        except DebugChannelBusy:
            raise RuntimeError("another program (WFInfo, Overwolf, DebugView, or a second VoidTrace) "
                               "is reading the game's log; VoidTrace will use the slower log file")
        running = find_warframe_pid() is not None
        return "live game log available" + ("" if running else " (Warframe is not running right now)")

    results.append(_check("Tesseract OCR", tesseract))
    results.append(_check("Warframe.Market", market))
    results.append(_check("Text reading", ocr))
    results.append(_check("Game log", game_log))
    return results


def format_results(results: list[Check]) -> str:
    lines = [f"{'OK  ' if ok else 'FAIL'}  {name}: {detail}" for name, ok, detail in results]
    lines.append(f"\nLog file: {log_path()}")
    return "\n".join(lines)
