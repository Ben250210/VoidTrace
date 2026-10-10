# VoidTrace

A read-only Warframe companion. It never writes to the game's files, never injects into the
process, and only reads `%LOCALAPPDATA%\Warframe\EE.log`.

## Status: Phase 1 (log watcher)

Tails `EE.log` and reports the moment the Void Fissure reward-choice screen opens.
Standard library only, no dependencies at runtime.

```
python 1_log_watcher.py                       # live
python 1_log_watcher.py --replay FILE         # replay a saved log
python tools/sample_log_lines.py --tail 40    # find candidate trigger lines
```

Tests:

```
pip install -r requirements-dev.txt
pytest
```

The trigger strings (`Pause countdown done`, `Got rewards`) are the ones WFInfo uses
(github.com/WFCD/WFinfo). They have not yet been observed in this machine's log; confirm
with `tools/sample_log_lines.py` after a relic run. See `voidtrace/log_watcher.py`.
