"""Shared fixtures.

The launcher remembers the language, theme, target and run history in an XDG
state file. Tests must never touch the person's real one, so every test runs
with the state directory pointed at a throwaway path - reads then find nothing
(the defaults apply) and writes land in the tmp tree.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield
