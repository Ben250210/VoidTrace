"""VoidTrace overlay app: log trigger -> capture -> OCR -> resolve -> price -> overlay.

    python main_app.py                     # watch EE.log, show prices on the reward screen
    python main_app.py --demo shot.png     # run the pipeline once on a screenshot and show it
    python main_app.py --trigger-now       # capture the screen immediately (for testing)

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

from voidtrace.cv_cleaner import TesseractNotFound, capture_screen
from voidtrace.items import load_items
from voidtrace.log_watcher import LogWatcher, RewardScreenEvent, default_log_path
from voidtrace.overlay import OverlayWindow, cards_summary
from voidtrace.pipeline import RewardCard, RewardPipeline
from voidtrace.price_cleaner import PriceService
from voidtrace.text_cleaner import EntityResolver

log = logging.getLogger("voidtrace")


class Controller(QObject):
    """Moves work off the UI thread and results back onto it via queued signals."""

    triggered = pyqtSignal()
    results_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, pipeline: RewardPipeline, overlay: OverlayWindow, capture_delay_ms: int) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.overlay = overlay
        self.capture_delay_ms = capture_delay_ms
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.triggered.connect(self._on_trigger)
        self.results_ready.connect(self._on_results)
        self.failed.connect(lambda msg: log.error("pipeline failed: %s", msg))

    def on_log_event(self, event: RewardScreenEvent) -> None:
        """Called on the log-watcher thread. Signals are thread-safe."""
        log.info("reward screen detected: %s", event.raw_line.strip())
        self.triggered.emit()

    def _on_trigger(self) -> None:
        # The reward cards animate in; wait before capturing.
        QTimer.singleShot(self.capture_delay_ms, self.run_once)

    def run_once(self, frame=None) -> None:
        if self.busy:
            return
        self.busy = True
        self.executor.submit(self._work, frame)

    def _work(self, frame) -> None:
        try:
            self.results_ready.emit(self.pipeline.run(frame))
        except TesseractNotFound as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # keep the app alive whatever a phase does
            log.exception("pipeline error")
            self.failed.emit(repr(exc))
        finally:
            self.busy = False

    def _on_results(self, cards: list[RewardCard]) -> None:
        log.info("rewards:\n%s", cards_summary(cards))
        self.overlay.show_cards(cards)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


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
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between trigger and capture")
    parser.add_argument("--hide-after", type=float, default=12.0, help="seconds the overlay stays up")
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
        watcher = LogWatcher(path, on_event=controller.on_log_event)
        watcher.start()
        log.info("watching %s", path)

    try:
        return app.exec()
    finally:
        if watcher is not None:
            watcher.stop()
        controller.shutdown()


if __name__ == "__main__":
    sys.exit(main())
