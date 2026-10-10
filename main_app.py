"""VoidTrace overlay app: log trigger -> capture -> OCR -> resolve -> price -> overlay.

    python main_app.py                     # watch the game's log, show prices on the reward screen
    python main_app.py --demo shot.png     # run the pipeline once on a screenshot and show it
    python main_app.py --trigger-now --any-window   # capture the screen immediately (for testing)

Quit from the tray icon, or Ctrl+C in the console.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import cv2
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from voidtrace.cv_cleaner import TesseractNotFound, capture_screen, warframe_is_foreground
from voidtrace.debug_log import DebugChannelBusy, DebugLogSource
from voidtrace.items import load_items
from voidtrace.log_watcher import LogTailer, LogWatcher, RewardScreenEvent, default_log_path
from voidtrace.overlay import OverlayWindow, cards_summary
from voidtrace.pipeline import PipelineSkipped, RewardCard, RewardPipeline
from voidtrace.price_cleaner import PriceService
from voidtrace.text_cleaner import EntityResolver

log = logging.getLogger("voidtrace")


class Controller(QObject):
    """Moves work off the UI thread and results back onto it via queued signals."""

    triggered = pyqtSignal()
    closed = pyqtSignal()
    results_ready = pyqtSignal(int, list)
    failed = pyqtSignal(str)

    def __init__(self, pipeline: RewardPipeline, overlay: OverlayWindow, capture_delay_ms: int) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.overlay = overlay
        self.capture_delay_ms = capture_delay_ms
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.generation = 0  # bumped on close so work for a closed screen is dropped
        self.triggered.connect(self._on_trigger)
        self.closed.connect(self._on_close)
        self.results_ready.connect(self._on_results)
        self.failed.connect(lambda msg: log.error("pipeline failed: %s", msg))

    def on_log_event(self, event: RewardScreenEvent) -> None:
        """Called on the log-watcher thread. Signals are thread-safe."""
        if event.kind == "close":
            log.info("reward screen closed")
            self.closed.emit()
            return
        log.info("reward screen detected: %s", event.raw_line.strip())
        self.triggered.emit()

    def _on_close(self) -> None:
        self.generation += 1
        self.overlay.clear()

    def _on_trigger(self) -> None:
        # The reward cards animate in; wait before capturing. The generation is taken NOW, so a
        # close that arrives during the wait cancels the capture (live test 2 captured the end
        # screen because it was checked only after the wait).
        generation = self.generation
        QTimer.singleShot(self.capture_delay_ms, lambda: self.run_once(generation=generation))

    def run_once(self, frame=None, generation: Optional[int] = None) -> None:
        if generation is None:
            generation = self.generation
        if generation != self.generation:
            log.info("reward screen closed before capture; skipping")
            return
        if self.busy:
            return
        self.busy = True
        self.executor.submit(self._work, frame, generation)

    def _work(self, frame, generation: int) -> None:
        try:
            cards = self.pipeline.read(frame)
            self.results_ready.emit(generation, cards)  # names + ducats first
            self.pipeline.price_cards(cards, on_update=lambda updated: self.results_ready.emit(generation, updated))
        except PipelineSkipped as exc:
            log.info("not showing: %s", exc)
        except TesseractNotFound as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # keep the app alive whatever a phase does
            log.exception("pipeline error")
            self.failed.emit(repr(exc))
        finally:
            self.busy = False

    def _on_results(self, generation: int, cards: list[RewardCard]) -> None:
        if generation != self.generation:
            log.info("reward screen already closed; not showing")
            return
        if not any(card.pending for card in cards):
            log.info("rewards:\n%s", cards_summary(cards))
        self.overlay.show_cards(cards)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


def make_line_source(kind: str, path: Path):
    """Live debug output when available (real time), else the EE.log file (can lag 10+ s)."""
    if kind in ("auto", "debug"):
        try:
            source = DebugLogSource()
            log.info("reading Warframe's live debug output")
            return source
        except (DebugChannelBusy, OSError) as exc:
            if kind == "debug":
                raise
            log.warning("live debug output unavailable (%s); falling back to %s, which can lag", exc, path)
    return LogTailer(path, start_at_eof=True)


def make_tray(app: QApplication) -> QSystemTrayIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(232, 190, 90))
    tray = QSystemTrayIcon(QIcon(pixmap), app)
    tray.setToolTip("VoidTrace")
    menu = QMenu()
    menu.addAction("Quit VoidTrace", app.quit)
    tray.setContextMenu(menu)
    tray.show()
    return tray


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace overlay")
    parser.add_argument("--path", type=Path, default=None, help="EE.log path")
    parser.add_argument("--demo", type=Path, default=None, help="run once on a screenshot instead of watching")
    parser.add_argument("--trigger-now", action="store_true", help="capture once right away")
    parser.add_argument("--players", type=int, default=None, help="squad size (default: detect)")
    parser.add_argument("--ui-scale", type=float, default=1.0, help="in-game UI scale, 1.0 = 100%%")
    parser.add_argument("--monitor", type=int, default=1, help="monitor to capture (1 = primary)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between trigger and capture")
    parser.add_argument("--source", choices=["auto", "debug", "file"], default="auto",
                        help="log source: live debug output (real time) or the EE.log file")
    parser.add_argument("--any-window", action="store_true",
                        help="capture even when Warframe is not the active window (testing)")
    parser.add_argument("--hide-after", type=float, default=20.0,
                        help="fallback: seconds the overlay stays up if the close line is missed")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Let Python handle Ctrl+C while Qt's loop runs.
    heartbeat = QTimer(interval=250)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()

    screen = app.primaryScreen()
    overlay = OverlayWindow(screen.geometry(), screen.devicePixelRatio(), args.ui_scale, int(args.hide_after * 1000))

    log.info("loading item list...")
    pipeline = RewardPipeline(
        EntityResolver(load_items()),
        PriceService(),
        capture=lambda: capture_screen(args.monitor),
        players=args.players,
        ui_scale=args.ui_scale,
        foreground_check=None if args.any_window else warframe_is_foreground,
    )
    controller = Controller(pipeline, overlay, int(args.delay * 1000))
    tray = make_tray(app)  # noqa: F841 (keep a reference)

    watcher = None
    if args.demo is not None:
        frame = cv2.imread(str(args.demo))
        if frame is None:
            print(f"could not read image: {args.demo}", file=sys.stderr)
            return 1
        controller.run_once(frame)
    elif args.trigger_now:
        controller.run_once()
    else:
        path = args.path or default_log_path()
        watcher = LogWatcher(path, on_event=controller.on_log_event, source=make_line_source(args.source, path))
        watcher.start()
        log.info("watching for relic reward screens")

    try:
        return app.exec()
    finally:
        if watcher is not None:
            watcher.stop()
        controller.shutdown()


if __name__ == "__main__":
    sys.exit(main())
