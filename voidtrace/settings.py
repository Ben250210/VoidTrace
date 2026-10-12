"""User settings kept between runs: %LOCALAPPDATA%\\VoidTrace\\settings.json.

    accent             the theme's accent colour (see theme.py)
    disclaimer_ok      the user clicked "I understand" on the first-run warning
    trade_updates      lower / remove your Warframe.Market orders after a trade (default on)
    alerts             price alerts: [{slug, name, max_price, rank, ranked}] (alerts.py)
    online_status      show you "in game" on Warframe.Market while Warframe runs (default off)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .items import default_cache_dir

log = logging.getLogger(__name__)


def settings_path() -> Path:
    return default_cache_dir() / "settings.json"


def load(path: Optional[Path] = None) -> dict[str, Any]:
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(key: str, default: Any = None, path: Optional[Path] = None) -> Any:
    return load(path).get(key, default)


def put(key: str, value: Any, path: Optional[Path] = None) -> None:
    path = path or settings_path()
    data = load(path)
    data[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # a read-only profile must not stop the app
        log.warning("could not save settings: %s", exc)
