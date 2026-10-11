<p align="center"><img src="docs/logo.png" width="128" alt="VoidTrace logo"></p>

# VoidTrace

Prices for Void relic rewards right on the reward screen, plus a Warframe.Market window to find
sellers and manage your own orders.

## Download (Windows)

1. Go to **[Releases](https://github.com/Ben250210/VoidTrace/releases/latest)** and download
   `VoidTrace-<version>-windows.zip`.
2. Unzip it anywhere (for example your Desktop) and open the `VoidTrace` folder.
3. Double-click **VoidTrace.exe**. A blue crystal icon appears in the system tray (bottom right).
   - If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
     The app is not code-signed; the source is all here.
4. In Warframe, set **Display Mode** to **Borderless Fullscreen**.
5. Open a relic: prices appear under the reward names. **Ctrl+Shift+M** opens the market.

No Python or other installs needed; OCR (Tesseract) is included. If something doesn't work,
right-click the tray icon, choose **Run self-test**, and send the result (and `voidtrace.log` from
**Open log folder**). Run only one of WFInfo / Overwolf / AlecaFrame / VoidTrace at a time: they
share the game's log channel.

## How it works

A read-only Warframe relic-reward companion, built as a set of data-cleaning pipelines.
It never writes to the game's files and never injects into the process. It reads
`%LOCALAPPDATA%\Warframe\EE.log`, takes a screenshot, and draws a click-through window
on top of the game.

```
EE.log ──► 1 log watcher ──► 2 capture + CV clean + OCR ──► 3 normalize + fuzzy resolve
                                                                       │
                     overlay ◄── 5 PyQt6 window ◄── 4 trade history + cleaned listings
```

| Phase | Module | Cleaning step |
|---|---|---|
| 1 | `voidtrace/log_watcher.py` | Tails EE.log; keyword prefilter + regex drop all but the reward-screen markers; debounces repeats; survives log rotation |
| 2 | `voidtrace/cv_cleaner.py` | Resolution-independent crop boxes (1080p/1440p/ultrawide); grayscale → blur → morphological illumination flattening → Otsu; auto-detects squad size |
| 3 | `voidtrace/text_cleaner.py`, `items.py` | Strips illegal chars, fixes leetspeak (`N3kr0s` → `Nekros`), drops stray glyphs and repeated words; RapidFuzz match against ~600 relic rewards; flags near-ties |
| 4 | `voidtrace/price_cleaner.py` | Price from closed trades (volume-weighted median, 48 h, else 7 days); listings as fallback: in-game sellers, log-scale Tukey IQR purge, median of the cheapest asks; local cache with stale fallback |
| 5 | `voidtrace/overlay.py`, `main_app.py` | Frameless, translucent, always-on-top, click-through overlay |

## Running from source

```
pip install -r requirements.txt -r requirements-dev.txt
winget install UB-Mannheim.TesseractOCR   # OCR engine (or set TESSERACT_CMD)
```

Run Warframe in **Borderless Fullscreen** so the overlay can sit on top.

Build the Windows release zip with `pip install pyinstaller` then `python tools/build_release.py`.

## Running

```
python main_app.py                        # full app: watch the log, show prices on the reward screen
python main_app.py --demo shot.png        # run once on a saved screenshot
python main_app.py --trigger-now          # capture the screen immediately
```

Each phase also has its own CLI for inspecting that stage:

```
python 1_log_watcher.py [--replay FILE]
python 2_cv_cleaner.py --image shot.png --debug-dir debug/
python 3_text_cleaner.py "N3kr0s Pr1m3" "AKSTlLETTO PRlME LINK"
python 4_price_cleaner.py "Nekros Prime Blueprint"
python tools/debug_log_probe.py --seconds 120   # measure EE.log delay vs live debug output
```

## Buying from players

- **Click a price card** on the reward screen to open that item's sellers. Only the cards take mouse input;
  every other click still goes to the game.
- **Ctrl+Shift+M** (or the tray icon) opens the market window for any item, mods included. Type to search;
  ranked items get a rank picker.
- **Click a seller** to copy warframe.market's whisper, then paste it in game chat:
  `/w Seller Hi! I want to buy: "Primed Continuity (rank 10)" for 45 platinum. (warframe.market)`
- Sellers in game come first, cheapest first. Prices the IQR filter flags as outliers stay listed, greyed out.
- Platinum and ducat icons are drawn by VoidTrace (`voidtrace/icons.py`), not taken from the game.

## Posting your own orders (optional)

Sign in from the market window (**Sign in to Warframe.Market**) to post and manage orders:

- **Post order** tab: the top 5 sell and buy orders for the rank you pick (like warframe.market's
  "Edit order" panel), then post at *match lowest*, *lowest +/- offset* (e.g. -1 to undercut) or a
  custom price. Your own listing is ignored when matching. Buying matches the highest buyer instead.
- **My orders** tab: your live orders, with *Match lowest* to reprice and *Delete*.

Warframe.Market has no OAuth for outside apps yet, so sign-in uses the same email + password login
as the website (`POST /v1/auth/signin`), and orders go to the v2 order endpoints with the session
token. The password is sent only to api.warframe.market and never stored; the token is kept in
Windows Credential Manager (`keyring`) and removed by **Sign out**. Nothing is posted automatically.

## Price cache

Local JSON in `%LOCALAPPDATA%\VoidTrace\`, fresh for 15 minutes, used as a stale fallback when the
API is unreachable.

## Tests

```
pytest
```

## Live test (2026-10-10)

One real 4-player relic run on a 2560x1600 laptop (`docs/live_test_2026-10-10.png`, squad names pixelated):

- **Trigger:** `ProjectionRewardChoice.lua: Got rewards` fired as the screen opened; `Relic reward screen shut down`
  15 s later now closes the overlay. Redacted excerpt: `tests/fixtures/live_2026-10-10_EE_excerpt.log`.
- **OCR:** all four names read correctly, live and when the screenshot is re-run offline.
- **Fixed:** prices came from the median asking price and were too high (Burston Prime Blueprint showed 14.5p,
  traded near 4p). They now come from closed trades. The cards covered the squad's pick list and now sit below it.

## Live test 2 (2026-10-10)

Cards arrived with 3 s left, then on the Mission Complete screen with OCR of a terminal window.

- **EE.log is buffered.** `tools/debug_log_probe.py` measured file lines arriving a median 0.1 s but up to
  11.2 s after the game's live debug output. VoidTrace now reads that debug output (`voidtrace/debug_log.py`,
  the channel DebugView and WFInfo use) and falls back to the file only if another program holds it.
- **Stale triggers.** An open that arrives with its close, or > 8 s behind the newest line, is dropped; a close
  during the capture delay cancels the capture; nothing is captured unless Warframe is the active window.
- **Confident wrong matches.** WRatio scored every "Prime ..." name 85.5 on messy text (Kestrel Prime Blade →
  Akstiletto Prime Blueprint). The matcher now averages token-set and plain similarity, cutoff 60, calibrated on
  22 labelled reads.
- **Speed.** Parallel OCR, concurrent price requests and progressive cards: names on screen ~1.3 s after
  capture, prices ~3.5 s (was ~9.5 s).

## Still unverified

- Other resolutions, UI scales, themes and non-English clients.
- The live-test-2 fixes and the card position below the squad list, which need a third live run.
- Capture waits until the names stop fading in (text-only check, since the 3D background keeps moving;
  capped at 2.5 s), then takes a second look at any unreadable or uncertain card. Both are tested offline
  on real name bands, not yet live.
