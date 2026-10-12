<p align="center"><img src="docs/logo.png" width="128" alt="VoidTrace logo"></p>

# VoidTrace

> [!WARNING]
> **Use at your own risk.** VoidTrace is an unofficial fan project, not affiliated with or endorsed by
> Digital Extremes (Warframe) or Warframe.Market. It only reads the game's log and your screen and never
> touches the game itself, but whether third-party tools are allowed is up to Digital Extremes. It comes
> with **no warranty**, and its authors are **not responsible for bans, suspensions, lost platinum or
> items, or any other loss** from using it. Full text: [DISCLAIMER.md](DISCLAIMER.md).

Prices for Void relic rewards right on the reward screen, plus one window for the rest: buy from
players and manage your Warframe.Market orders, read, grade and price rivens, and see which relics
are worth opening.

## Install (3 steps, Windows)

1. Download `VoidTrace-<version>-windows.zip` from **[Releases](https://github.com/Ben250210/VoidTrace/releases/latest)**.
2. Right-click it > **Extract All**, anywhere (for example your Desktop).
3. Open the `VoidTrace` folder and double-click **VoidTrace.exe**. A blue crystal appears in the
   system tray (bottom right).
   - If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
     The app is not code-signed; the source is all here.

Nothing else to install: OCR (Tesseract) is included. In Warframe, set **Display Mode** to
**Borderless Fullscreen**. Run only one of WFInfo / Overwolf / AlecaFrame / VoidTrace at a time:
they share the game's log channel. If something doesn't work, right-click the tray icon >
**Run self-test**, and send the result (and `voidtrace.log` from **Open log folder**).

## Using it

- **Relic rewards:** open a relic; prices appear under the reward names. Click a price card for its sellers.
- **The VoidTrace window:** click the tray icon. A sidebar on the left holds every page:

  | Market | Rivens | Relics |
  |---|---|---|
  | Buy · Sell · My orders · Trades · Alerts | Find · Price check · Sell · My rivens | Unvaulted · Vaulted · Look up |

  At the bottom: **Sign in to Warframe.Market** and **Accent colour**. The window reopens on the last page you used.
- **Hotkeys (in game):** **Ctrl+Shift+M** opens Market > Buy; **Ctrl+Shift+R** reads the riven on
  screen into Rivens > Price check.
- **Tray menu (right-click):** Open VoidTrace; the riven popup, order updates after trades and online
  status toggles; accent colour; self-test; log folder; disclaimer; quit.

## Market

- **Buy:** search any item, mods included (ranked items get a rank picker). Sellers in game come first,
  cheapest first; prices the IQR filter flags as outliers stay listed, greyed out. **Click a seller** to
  copy the whisper, then paste it in game chat:
  `/w Seller Hi! I want to buy: "Primed Continuity (rank 10)" for 45 platinum. (warframe.market through VoidTrace)`
- **Sell** (signed in): the top 5 sell and buy orders for the rank you pick (like warframe.market's
  "Edit order" panel), then post at *match lowest*, *lowest +/- offset* (e.g. -1 to undercut) or a
  custom price. Your own listing is ignored when matching.
- **My orders:** your live orders, with *Match lowest* to reprice and *Delete*.
- **After a trade:** when a trade goes through in game, VoidTrace reads it from the game log and lowers
  the matching order by the amount traded (items you gave lower your sell orders, items you received
  your buy orders), or removes it when none are left. A card at the top right shows what changed, with
  **Undo** for 10 seconds. Turn it off in the tray menu (**Update orders after trades**).
- **Trades:** every trade VoidTrace saw (signed in or not), newest first, with plat earned, spent and
  net for today, the last 7 days and all time. Kept only on this PC (`trades.jsonl`).
- **Alerts:** on Buy, click **Alert me…** and pick a price (it starts one below the cheapest seller).
  Every minute VoidTrace checks the sellers; when someone online lists it at or under your price, a
  tray message appears, and clicking it copies the whisper. Each seller and price is announced once,
  and troll prices are skipped.

Signing in: Warframe.Market has no OAuth for outside apps yet, so it uses the same email + password
login as the website (`POST /v1/auth/signin`), and orders go to the v2 order endpoints with the
session token. The password is sent only to api.warframe.market and never stored; the token is kept in
Windows Credential Manager (`keyring`) and removed by **Sign out**. Nothing is posted automatically;
the only automatic change is lowering an order after a trade (above).

## Rivens

With a riven on screen, in **Inventory > Mods > Cycle** or in **Item Details**, Ctrl+Shift+R reads the
card and prices it.

**Riven popup:** you don't even need the hotkey. While a riven is open on the Cycle screen or in Item
Details, a small panel appears at the right edge with its stats, how well each rolled and a price
estimate; click it for the full price check. VoidTrace notices the card by the emblem at its top (the
screen is checked about every 0.7 s, only while Warframe is the active window). Turn it off in the tray menu.

- **Find:** auctions for a weapon, filtered by wanted stats, curse, buyout only and clean (never
  rolled) / rolled. Click one to copy a whisper that names the riven:
  `/w Seller Hi! I'd like to buy your Ocucor Sati-toxitio riven for 1500 platinum. (warframe.market through VoidTrace)`
- **Price check:** type the riven's name and its buffs fill in (the name spells them: Igni-insitron =
  Heat + Puncture + Damage to Corpus), or read it off the screen. Each value gets a letter grade for how
  well it rolled, and the estimate comes from listings with the same stats that rolled about as well.
  Rivens have no trade history, so these are asking prices.
- **Sell** and **My rivens** (signed in): post a riven auction in one click, show/hide it, match the
  cheapest comparable listing, close it.

Grades use the game's own base values (Warframe's Public Export, `voidtrace/data/riven_bases.json`,
refreshed with `python tools/update_riven_bases.py`):
`value = base x 10 x (rank + 1) x disposition x buff-count multiplier x roll`, roll 0.9-1.1.
Screen reading is tested on real cards in Item Details and on the Cycle screen (where rolls come from
the Kuva price) at 2560x1600, 1920x1200 and 1680x1050; 16:9 screens and other UI scales are not
verified yet.

## Relics

- **Unvaulted / Vaulted:** every relic that drops now (green chest) or no longer drops (red chest),
  with its average plat at Intact and Radiant, its ducats, and its best reward. Click a column header to
  sort by it, click again to flip. Double-click a relic for its rewards.
- **Look up:** search a relic (`axi a1`) or a reward (`nikana prime`): its value in plat and ducats at
  Intact, Exceptional, Flawless and Radiant, and its 6 rewards with their prices and chances.
- **Solo / Squad of 4** (top right): a squad's value is the best of 4 drops.

Cached prices show straight away; fresh ones load in the background (the first full load of the
Vaulted page takes a few minutes). Relic contents come from Warframe's Public Export and vaulting from
Warframe.Market: `python tools/update_relics.py` refreshes `voidtrace/data/relics.json`.

## Online status (optional)

Tray > **Show me in game on Warframe.Market while playing** (off by default). While you are signed in,
VoidTrace sets your Warframe.Market status to *In game* when Warframe starts and puts back what you
had (Online or Invisible) when it closes or when VoidTrace quits, so buyers only see you while you can
trade. It uses Warframe.Market's websocket (`@wfm|cmd/status/set`), opening a short connection for each
change. *In game* is set for one hour at a time and renewed every 30 minutes, so if VoidTrace crashes
or the PC sleeps the status falls back by itself.

## Discord notifications (optional)

Get new whispers, price alerts and finished trades in your own Discord channel (and on your phone).
In Discord: Server Settings > Integrations > Webhooks > New Webhook > pick a channel > **Copy Webhook
URL**. In VoidTrace: **Discord** at the bottom of the sidebar > paste the link > **Send test**, and
tick what to send. The link is kept in Windows Credential Manager. A new whisper also shows a Windows
tray message: "New conversation with {name}" (the message text itself is not in the game's log).

## Accent colour

**Accent colour** at the bottom of the sidebar (or in the tray menu): a preset (Void blue, Cyan,
Purple, Pink, Gold, Green, Red) or any colour with **Custom...**. Everything changes at once and the
choice is remembered (`%LOCALAPPDATA%\VoidTrace\settings.json`).

## Run from source

```
pip install -r requirements.txt
winget install UB-Mannheim.TesseractOCR   # OCR engine (or set TESSERACT_CMD)
python main_app.py
```

```
python main_app.py --demo shot.png        # run the reward pipeline once on a saved screenshot
python main_app.py --trigger-now          # capture the screen immediately
python main_app.py --self-test            # check OCR, Warframe.Market and the game log
```

Tests: `pip install -r requirements-dev.txt`, then `pytest`. Build the Windows zip with
`pip install pyinstaller` then `python tools/build_release.py`.

## How it works

Read-only towards the game: it never writes to the game's files and never injects into the process.
It reads `%LOCALAPPDATA%\Warframe\EE.log` (or the game's live debug output), takes screenshots, and
draws its own windows on top.

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

The window's pages live in `voidtrace/ui/` (one file per page or group of pages; shared table, style and
section code in `ui/widgets.py`). Each phase also has its own command-line tool for inspecting that stage:

```
python tools/stages/1_log_watcher.py [--replay FILE]
python tools/stages/2_cv_cleaner.py --image shot.png --debug-dir debug/
python tools/stages/3_text_cleaner.py "N3kr0s Pr1m3" "AKSTlLETTO PRlME LINK"
python tools/stages/4_price_cleaner.py "Nekros Prime Blueprint"
python tools/demo.py --offline            # an HTML report of what each cleaning step fixes
```

Prices are cached in `%LOCALAPPDATA%\VoidTrace\`, fresh for 15 minutes, and used as a fallback when
the API is unreachable. Platinum, ducat and chest icons are drawn by VoidTrace (`voidtrace/icons.py`),
not taken from the game.

## Live test (2026-10-10)

One real 4-player relic run on a 2560x1600 laptop (`docs/live_test_2026-10-10.png`, squad names pixelated):

- **Trigger:** `ProjectionRewardChoice.lua: Got rewards` fired as the screen opened; `Relic reward screen shut down`
  15 s later now closes the overlay. Redacted excerpt: `tests/fixtures/live_2026-10-10_EE_excerpt.log`.
- **OCR:** all four names read correctly, live and when the screenshot is re-run offline.
- **Fixed:** prices came from the median asking price and were too high (Burston Prime Blueprint showed 14.5p,
  traded near 4p). They now come from closed trades. The cards covered the squad's pick list and now sit below it.

## Live test 2 (2026-10-10)

Cards arrived with 3 s left, then on the Mission Complete screen with OCR of a terminal window.

- **EE.log is buffered.** A probe measured file lines arriving a median 0.1 s but up to 11.2 s after the
  game's live debug output. VoidTrace now reads that debug output (`voidtrace/debug_log.py`, the channel
  DebugView and WFInfo use) and falls back to the file only if another program holds it.
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
