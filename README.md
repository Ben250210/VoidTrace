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

The trigger strings (`Choice.swf`, `Choice.lua`) come from the project spec and are not yet
confirmed against real game output. See `voidtrace/log_watcher.py`.
