"""Simple daily cap on live agent runs, so a public deployment can't run up the API bill.

MAX_RUNS_PER_DAY (env, default 0 = unlimited). Counted per server in sandbox/_usage.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()


def _limit() -> int:
    try:
        return int(os.getenv("MAX_RUNS_PER_DAY", "0"))
    except ValueError:
        return 0


def _file(root: Path) -> Path:
    return root / "_usage.json"


def runs_left(root: Path) -> int | None:
    limit = _limit()
    if limit <= 0:
        return None
    today = dt.date.today().isoformat()
    try:
        data = json.loads(_file(root).read_text())
    except Exception:
        data = {}
    return max(0, limit - int(data.get(today, 0)))


def take_run(root: Path) -> bool:
    """Reserve one run. Returns False when today's cap is reached."""
    limit = _limit()
    if limit <= 0:
        return True
    today = dt.date.today().isoformat()
    with _lock:
        root.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(_file(root).read_text())
        except Exception:
            data = {}
        used = int(data.get(today, 0))
        if used >= limit:
            return False
        _file(root).write_text(json.dumps({today: used + 1}))
        return True
