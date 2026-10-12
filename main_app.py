"""VoidTrace: starts everything and wires it together.

The tray icon, the one VoidTrace window (Market, Rivens, Relics: voidtrace/ui/main_window.py), and the
in-game helpers: reward-screen prices (log trigger -> capture -> OCR -> resolve -> price -> overlay),
the riven popup, the trade card, price alerts and the online status.

    python main_app.py                     # watch the game's log, show prices on the reward screen
    python main_app.py --demo shot.png     # run the pipeline once on a screenshot and show it
    python main_app.py --trigger-now --any-window   # capture the screen immediately (for testing)
    python main_app.py --self-test         # check OCR, Warframe.Market and the game log, then exit

Quit from the tray icon, or Ctrl+C in the console.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import cv2
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from voidtrace.cv_cleaner import TesseractNotFound, capture_screen, warframe_is_foreground
from voidtrace.debug_log import DebugChannelBusy, DebugLogSource, find_warframe_pid
from voidtrace import app_support, disclaimer, hotkey, online_status, settings, theme, trade_toast
from voidtrace.icons import voidtrace_logo
from voidtrace.items import load_catalog, load_items
from voidtrace.ui.market import MarketSection
from voidtrace.rivens import RivenService, load_riven_catalog
from voidtrace.sellers import SellerService
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


def app_icon() -> QIcon:
    """The VoidTrace logo at the sizes Windows asks for (tray, title bar, taskbar)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(voidtrace_logo(size))
    return icon


def make_tray(app: QApplication, open_window, game_log_status=lambda: None, toggle_riven_popup=None,
              toggle_trade_updates=None, toggle_online_status=None) -> QSystemTrayIcon:
    """The tray icon: click it to open VoidTrace; right-click for the toggles and the rest."""
    from voidtrace.ui.main_window import accent_menu

    tray = QSystemTrayIcon(app_icon(), app)
    tray.setToolTip("VoidTrace")
    menu = QMenu()
    menu.addAction("Open VoidTrace", open_window)
    menu.addSeparator()
    for label, toggle, on in (("Riven popup in Cycle / Item Details", toggle_riven_popup, True),
                              ("Update orders after trades", toggle_trade_updates, trade_toast.enabled()),
                              ("Show me in game on Warframe.Market while playing", toggle_online_status,
                               online_status.enabled())):
        if toggle is not None:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(on)
            action.toggled.connect(toggle)
    menu.addMenu(accent_menu(menu))
    menu.addSeparator()
    menu.addAction("Run self-test", lambda: show_self_test(game_log_status()))
    menu.addAction("Open log folder", lambda: _open_folder(app_support.log_path().parent))
    menu.addAction("Disclaimer (use at your own risk)", lambda: show_disclaimer(first_run=False))
    menu.addSeparator()
    menu.addAction("Quit VoidTrace", app.quit)
    tray.activated.connect(lambda reason: open_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.setContextMenu(menu)
    tray.show()
    return tray


def show_disclaimer(first_run: bool) -> bool:
    """The use-at-your-own-risk notice. On first run it must be accepted; returns False to quit."""
    box = QMessageBox()
    box.setWindowTitle(disclaimer.TITLE)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(disclaimer.SHORT)
    if not first_run:
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        return True
    agree = box.addButton("I understand, start VoidTrace", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is not agree:
        return False
    settings.put("disclaimer_ok", True)
    return True


def _open_folder(path: Path) -> None:
    import os

    os.startfile(str(path))  # noqa: S606 - opens Explorer on our own log folder


def show_self_test(game_log_status: Optional[str] = None) -> bool:
    results = app_support.self_test(game_log_status)
    text = app_support.format_results(results)
    log.info("self-test:\n%s", text)
    if sys.stdout is not None:
        print(text)
    ok = all(passed for _, passed, _ in results)
    box = QMessageBox(QMessageBox.Icon.Information if ok else QMessageBox.Icon.Warning, "VoidTrace self-test",
                      ("Everything VoidTrace needs is working.\n\n" if ok else
                       "Something VoidTrace needs is not working:\n\n") + text)
    box.exec()
    return ok


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VoidTrace overlay")
    parser.add_argument("--path", type=Path, default=None, help="EE.log path")
    parser.add_argument("--demo", type=Path, default=None, help="run once on a screenshot instead of watching")
    parser.add_argument("--trigger-now", action="store_true", help="capture once right away")
    parser.add_argument("--players", type=int, default=None, help="squad size (default: detect)")
    parser.add_argument("--ui-scale", type=float, default=1.0, help="in-game UI scale, 1.0 = 100%%")
    parser.add_argument("--monitor", type=int, default=1, help="monitor to capture (1 = primary)")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="seconds before checking whether the cards have finished fading in")
    parser.add_argument("--source", choices=["auto", "debug", "file"], default="auto",
                        help="log source: live debug output (real time) or the EE.log file")
    parser.add_argument("--riven-shot", type=Path, default=None,
                        help="open Rivens > Price check and read the riven on this screenshot")
    parser.add_argument("--any-window", action="store_true",
                        help="capture even when Warframe is not the active window (testing)")
    parser.add_argument("--hide-after", type=float, default=20.0,
                        help="fallback: seconds the overlay stays up if the close line is missed")
    parser.add_argument("--no-hotkey", action="store_true", help="do not register Ctrl+Shift+M")
    parser.add_argument("--self-test", action="store_true",
                        help="check OCR, Warframe.Market and the game log, show the result and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_file = app_support.setup_logging(args.verbose)
    log.info("VoidTrace starting (log: %s)", log_file)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    if args.self_test:
        return 0 if show_self_test() else 1

    if not settings.get("disclaimer_ok") and not show_disclaimer(first_run=True):
        return 0
    instance_lock = app_support.acquire_single_instance()  # noqa: F841 (held until exit)
    if instance_lock is None:
        QMessageBox.information(None, "VoidTrace", "VoidTrace is already running.\n\n"
                                "Look for the blue crystal icon in the system tray (bottom right, near the clock). "
                                "Click the tray icon to open it.")
        return 0
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Let Python handle Ctrl+C while Qt's loop runs.
    heartbeat = QTimer(interval=250)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()

    screen = app.primaryScreen()
    overlay = OverlayWindow(screen.geometry(), screen.devicePixelRatio(), args.ui_scale, int(args.hide_after * 1000))

    log.info("loading item list...")
    reward_items = load_items()
    pipeline = RewardPipeline(
        EntityResolver(reward_items),
        PriceService(),
        capture=lambda: capture_screen(args.monitor),
        players=args.players,
        ui_scale=args.ui_scale,
        foreground_check=None if args.any_window else warframe_is_foreground,
    )
    controller = Controller(pipeline, overlay, int(args.delay * 1000))

    from voidtrace.ui.main_window import MainWindow
    from voidtrace.ui.relics import RelicSection
    from voidtrace.ui.rivens import RivenSection

    market = MarketSection(load_catalog(), SellerService())
    riven_data_cache: dict = {}
    riven_data_lock = threading.Lock()

    def riven_data():
        """Riven weapons/stats and the auction search, loaded once (on first use: one more download)."""
        with riven_data_lock:
            if not riven_data_cache:
                riven_data_cache["catalog"] = load_riven_catalog(market.sellers.client)
                riven_data_cache["service"] = RivenService(market.sellers.client)
            return riven_data_cache["catalog"], riven_data_cache["service"]

    from voidtrace.discord import DiscordNotifier
    from voidtrace.whispers import WhisperWatcher

    discord = DiscordNotifier()
    window = MainWindow(market, RelicSection(reward_items, pipeline.prices), riven_data,
                        lambda data: RivenSection(*data, account=market.account), discord=discord)

    def riven_hotkey() -> None:
        """Ctrl+Shift+R: in game, read the riven on screen (Item Details view); else just open Rivens."""
        frame = None
        if args.any_window or warframe_is_foreground():
            try:
                frame = capture_screen(args.monitor)  # before the window opens on top of the card
            except Exception as exc:
                log.warning("riven capture failed: %s", exc)
        if frame is None:
            window.open_page("rivens/price")
        else:
            window.read_riven(frame)

    def open_riven_details(draft, _check) -> None:
        window.open_riven_draft(draft)

    from voidtrace.riven_popup import RivenAssistant, RivenPopup
    from voidtrace.riven_watch import RivenScreenWatcher

    riven_popup = RivenPopup(screen.geometry())
    theme.on_change(lambda: (riven_popup.update(), overlay.update()))  # painted in the accent
    riven_assistant = RivenAssistant(riven_data, riven_popup,
                                     lambda: market.account.ingame_name if market.account.signed_in else None,
                                     open_riven_details)
    riven_watcher = RivenScreenWatcher(lambda: capture_screen(args.monitor),
                                       None if args.any_window else warframe_is_foreground)
    riven_watcher.card_shown.connect(riven_assistant.card_shown)
    riven_watcher.card_hidden.connect(riven_assistant.card_hidden)
    riven_watcher.card_back.connect(riven_assistant.card_back)

    def toggle_riven_popup(on: bool) -> None:
        if on:
            riven_watcher.start()
        else:
            riven_watcher.stop()
        log.info("riven popup %s", "on" if on else "off")

    def open_card(slot: int) -> None:
        item = overlay.cards[slot].resolution.item
        if item is not None and item.slug is not None:
            window.open_item(item.slug)

    overlay.card_clicked.connect(open_card)
    watcher = None

    def refresh_my_orders() -> None:
        if window.isVisible() and window.current_page() == "market/orders":
            market.orders_panel.refresh()

    trades = trade_toast.TradeTracker(market.account, market.catalog, trade_toast.TradeToast(screen.geometry()),
                                      on_orders_changed=refresh_my_orders)
    trades.recorded.connect(market.trades_panel.add)
    from voidtrace.trades import summary as trade_summary

    trades.recorded.connect(lambda record: discord.send(
        "trades", "\U0001f91d Trade done", trade_summary(record.trade, [])))

    status_keeper = online_status.StatusKeeper(
        lambda: market.account.token if market.account.signed_in else None,
        lambda: find_warframe_pid() is not None)

    def toggle_online_status(on: bool) -> None:
        settings.put(online_status.SETTING, on)
        log.info("in-game status on Warframe.Market %s", "on" if on else "off")
        status_keeper.tick()

    def toggle_trade_updates(on: bool) -> None:
        settings.put(trade_toast.SETTING, on)
        log.info("order updates after trades %s", "on" if on else "off")

    def game_log_status() -> Optional[str]:
        if watcher is None:
            return None
        if isinstance(watcher.source, DebugLogSource):
            return "live game log in use by this VoidTrace"
        return ("FAIL: using the slower log file: another program (WFInfo, Overwolf, DebugView) "
                "was reading the game's log when VoidTrace started")

    tray = make_tray(app, window.open_page, game_log_status, toggle_riven_popup, toggle_trade_updates, toggle_online_status)
    status_keeper.changed.connect(lambda text: tray.setToolTip(f"VoidTrace\n{text}"))
    status_keeper.start()
    riven_watcher.start()
    tray.showMessage("VoidTrace is running",
                     "Prices appear on relic reward screens (Borderless Fullscreen). Click this icon to open VoidTrace.",
                     app_icon(), 7000)
    from voidtrace.alerts import AlertWatcher
    from voidtrace.sellers import whisper_text

    alert_watcher = AlertWatcher(market.sellers,
                                 lambda: market.account.ingame_name if market.account.signed_in else None)
    market.alerts_changed.connect(alert_watcher.check_now)
    pending_whisper: dict = {}

    def alert_found(alert, seller) -> None:
        pending_whisper["text"] = whisper_text(seller, alert.name, ranked=alert.ranked)
        tray.showMessage(f"{alert.label}: {seller.platinum}p from {seller.ingame_name}",
                         "Click here to copy the whisper, then paste it in game chat.", app_icon(), 10000)
        discord.send("alerts", f"\U0001f514 {alert.label}: {seller.platinum}p",
                     f"from **{seller.ingame_name}**\n`{pending_whisper['text']}`")

    whispers = WhisperWatcher()

    def new_whisper(name: str) -> None:
        tray.showMessage("New whisper", f"New conversation with {name}", app_icon(), 8000)
        discord.send("whispers", "\U0001f4ac New conversation", f"with **{name}**")

    whispers.new_conversation.connect(new_whisper)

    def tray_message_clicked() -> None:
        text = pending_whisper.pop("text", None)
        if text:
            QApplication.clipboard().setText(text)

    alert_watcher.found.connect(alert_found)
    tray.messageClicked.connect(tray_message_clicked)
    alert_watcher.start()
    hotkey_filter = None if args.no_hotkey else hotkey.register(app, lambda: window.open_page("market/buy"))  # noqa: F841
    riven_hotkey = None if args.no_hotkey else hotkey.register(  # noqa: F841
        app, riven_hotkey, key="R", hotkey_id=hotkey.RIVEN_HOTKEY_ID)

    if args.riven_shot is not None:
        shot = cv2.imread(str(args.riven_shot))
        if shot is None:
            print(f"could not read image: {args.riven_shot}", file=sys.stderr)
            return 1
        window.read_riven(shot)

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
        watcher = LogWatcher(path, on_event=controller.on_log_event, source=make_line_source(args.source, path),
                             on_line=lambda line: (trades.feed(line), whispers.feed(line)))
        watcher.start()
        log.info("watching for relic reward screens")

    try:
        return app.exec()
    finally:
        if watcher is not None:
            watcher.stop()
        controller.shutdown()
        window.shutdown()
        riven_watcher.stop()
        riven_assistant.shutdown()
        trades.shutdown()
        alert_watcher.shutdown()
        discord.shutdown()
        status_keeper.stop()
        hotkey.unregister()
        hotkey.unregister(hotkey.RIVEN_HOTKEY_ID)


if __name__ == "__main__":
    sys.exit(main())
