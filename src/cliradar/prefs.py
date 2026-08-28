"""Small on-disk memory for the launcher: language, theme, target, runs.

The menu used to forget everything the moment it exited - the operator re-picked
the language, re-picked the theme and re-typed the device path on every start.
This module keeps those few answers in a state file under the XDG state
directory, deliberately outside the repository: it is per-person UI memory, not
project configuration, and it must never end up in a commit.

Nothing here is a secret store. Only the choices that are already visible on the
launcher are written - a config path, two preference words and a short run
history - so the file staying readable is a feature, not a leak. Every read and
write is best-effort: a missing, unreadable or corrupt state file degrades to
the defaults rather than taking the menu down with it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# How many past runs to keep. Long enough to answer "what did I do last time
# and did it finish", short enough that the file stays a glance, not a log.
MAX_RUNS = 5

_DEFAULTS: dict[str, Any] = {"lang": "en", "theme": "dark", "config": "", "runs": []}


def state_path() -> Path:
    """Where the state file lives, honouring XDG_STATE_HOME when it is set."""
    base = os.environ.get("XDG_STATE_HOME") or ""
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "cliradar" / "menu.json"


def load_prefs() -> dict[str, Any]:
    """Read the state file, filling in defaults for anything missing or bad.

    The file is written by this process and by no one else, but it is still
    treated as untrusted input: a hand-edited value of the wrong type is dropped
    in favour of the default instead of reaching the render code.
    """
    prefs = dict(_DEFAULTS)
    prefs["runs"] = []
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prefs
    if not isinstance(raw, dict):
        return prefs
    if raw.get("lang") in ("en", "ru"):
        prefs["lang"] = raw["lang"]
    if raw.get("theme") in ("dark", "light"):
        prefs["theme"] = raw["theme"]
    if isinstance(raw.get("config"), str):
        prefs["config"] = raw["config"]
    runs = raw.get("runs")
    if isinstance(runs, list):
        prefs["runs"] = [
            {"mode": str(item.get("mode", "")), "at": str(item.get("at", ""))}
            for item in runs[:MAX_RUNS]
            if isinstance(item, dict) and item.get("mode")
        ]
    return prefs


def save_prefs(prefs: dict[str, Any]) -> bool:
    """Persist the state file; returns False when the write could not happen.

    Written to a sibling temp file and moved into place, so an interrupted write
    leaves the previous state intact rather than a half-written file the next
    start would have to discard.
    """
    path = state_path()
    payload = {key: prefs.get(key, _DEFAULTS[key]) for key in _DEFAULTS}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(path)
    except OSError:
        return False
    return True


def remember_run(prefs: dict[str, Any], mode: str, at: str) -> dict[str, Any]:
    """Push a run onto the front of the history, newest first, capped.

    Pure: the caller decides when to save. ``at`` is supplied by the caller
    rather than read from the clock here, which keeps the function testable and
    lets the menu format the stamp in one place.
    """
    runs = [dict(item) for item in prefs.get("runs", []) if isinstance(item, dict)]
    runs.insert(0, {"mode": mode, "at": at})
    prefs["runs"] = runs[:MAX_RUNS]
    return prefs
