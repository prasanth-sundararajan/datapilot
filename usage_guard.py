"""Daily run limit: at most MAX_RUNS_PER_DAY live agent runs per day (default 3).

- A run (a question, a follow-up, or "let the agents continue") is counted the moment it starts.
- The count resets at midnight India time (IST, UTC+5:30).
- Demo mode spends nothing and is not counted.
- Stored in SANDBOX_DIR/_usage.json, updated under a lock.

Settings (environment):
    MAX_RUNS_PER_DAY    runs allowed per day, default 3 (0 = no limit)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_lock = threading.Lock()


def limit() -> int:
    try:
        return max(0, int(os.getenv("MAX_RUNS_PER_DAY", "3")))
    except ValueError:
        return 3


def _today() -> str:
    return dt.datetime.now(IST).date().isoformat()


def _file(root: Path) -> Path:
    return root / "_usage.json"


def _used(root: Path) -> int:
    try:
        return int(json.loads(_file(root).read_text()).get(_today(), 0))
    except Exception:
        return 0


def runs_used(root: Path) -> int:
    return _used(root)


def runs_left(root: Path) -> int | None:
    lim = limit()
    return None if lim <= 0 else max(0, lim - _used(root))


def take_run(root: Path) -> bool:
    """Reserve one run. Returns False when today's limit is reached."""
    lim = limit()
    if lim <= 0:
        return True
    with _lock:
        root.mkdir(parents=True, exist_ok=True)
        used = _used(root)
        if used >= lim:
            return False
        _file(root).write_text(json.dumps({_today(): used + 1}))
        return True
