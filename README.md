# VoidTrace

A read-only Warframe relic-reward companion, built as a set of data-cleaning pipelines.
It never writes to the game's files and never injects into the process. It reads
`%LOCALAPPDATA%\Warframe\EE.log`, takes a screenshot, and draws a click-through window
on top of the game.

```
EE.log ──► 1 log watcher ──► 2 capture + CV clean + OCR ──► 3 normalize + fuzzy resolve
                                                                       │
                     overlay ◄── 5 PyQt6 window ◄── 4 market orders + IQR purge
```

| Phase | Module | Cleaning step |
|---|---|---|
| 1 | `voidtrace/log_watcher.py` | Tails EE.log; keyword prefilter + regex drop all but the reward-screen markers; debounces repeats; survives log rotation |
| 2 | `voidtrace/cv_cleaner.py` | Resolution-independent crop boxes (1080p/1440p/ultrawide); grayscale → blur → morphological illumination flattening → Otsu; auto-detects squad size |
| 3 | `voidtrace/text_cleaner.py`, `items.py` | Strips illegal chars, fixes leetspeak (`N3kr0s` → `Nekros`), drops stray glyphs and repeated words; RapidFuzz match against ~600 relic rewards; flags near-ties |
| 4 | `voidtrace/price_cleaner.py` | Prefers in-game sellers; Tukey IQR fences purge lowball/gouge listings; median price; local or Supabase cache with stale fallback |
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

## Not yet verified against the live game

- **Log trigger:** `Pause countdown done` / `Got rewards` are WFInfo's markers
  (github.com/WFCD/WFinfo). They haven't been observed in this machine's log yet.
- **Crop geometry:** follows WFInfo's 1080p reference layout. It's been tested on synthetic
  screens only. Check with `2_cv_cleaner.py --image <real screenshot> --debug-dir debug/`.
- **Capture delay:** the 1.5 s pause before the screenshot is a starting guess (`--delay`).
