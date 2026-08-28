"""The launcher's between-starts memory: language, theme, target, run history."""

from __future__ import annotations

import json
from pathlib import Path

from cliradar.prefs import MAX_RUNS, load_prefs, remember_run, save_prefs, state_path


def test_state_path_follows_xdg_state_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "x"))
    assert state_path() == tmp_path / "x" / "cliradar" / "menu.json"


def test_state_path_falls_back_to_local_state(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert state_path() == tmp_path / ".local" / "state" / "cliradar" / "menu.json"


def test_defaults_when_nothing_is_saved() -> None:
    assert load_prefs() == {"lang": "en", "theme": "dark", "config": "", "runs": []}


def test_saved_preferences_come_back(tmp_path) -> None:
    prefs = {
        "lang": "ru",
        "theme": "light",
        "config": str(tmp_path / "sw.yml"),
        "runs": [{"mode": "audit", "at": "2026-08-27 21:04"}],
    }
    assert save_prefs(prefs) is True
    assert load_prefs() == prefs


def test_unknown_values_degrade_to_defaults() -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lang": "kl", "theme": 7, "config": 3, "runs": "no"}))
    assert load_prefs() == {"lang": "en", "theme": "dark", "config": "", "runs": []}


def test_a_corrupt_state_file_is_not_fatal() -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert load_prefs()["lang"] == "en"


def test_save_reports_failure_instead_of_raising(tmp_path, monkeypatch) -> None:
    # A state directory that cannot be created (a file sits where it belongs).
    blocker = tmp_path / "blocked"
    blocker.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert save_prefs({"lang": "ru"}) is False


def test_remember_run_keeps_the_newest_first_and_caps() -> None:
    prefs = {"runs": []}
    for index in range(MAX_RUNS + 3):
        remember_run(prefs, f"mode{index}", f"at{index}")
    assert len(prefs["runs"]) == MAX_RUNS
    assert prefs["runs"][0] == {"mode": f"mode{MAX_RUNS + 2}", "at": f"at{MAX_RUNS + 2}"}


def test_history_is_trimmed_on_read_too() -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = [{"mode": "audit", "at": str(i)} for i in range(MAX_RUNS + 4)]
    path.write_text(json.dumps({"runs": runs}))
    assert len(load_prefs()["runs"]) == MAX_RUNS
