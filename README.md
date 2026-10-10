# VoidTrace

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
| 4 | `voidtrace/price_cleaner.py` | Price from closed trades (volume-weighted median, 48 h, else 7 days); listings as fallback: in-game sellers, log-scale Tukey IQR purge, median of the cheapest asks; local or Supabase cache with stale fallback |
| 5 | `voidtrace/overlay.py`, `main_app.py` | Frameless, translucent, always-on-top, click-through overlay |

## Setup

```
pip install -r requirements.txt -r requirements-dev.txt
winget install UB-Mannheim.TesseractOCR   # OCR engine (or set TESSERACT_CMD)
```

Run Warframe in **Borderless Fullscreen** so the overlay can sit on top.

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
python tools/sample_log_lines.py --tail 40
```

## Price cache

Local JSON in `%LOCALAPPDATA%\VoidTrace\` by default. To share a cache through Supabase,
apply `supabase/migrations/001_price_cache.sql` to a project and set `SUPABASE_URL` and
`SUPABASE_KEY` (the publishable key).

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
