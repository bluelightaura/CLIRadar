"""Excerpts from a real manual, kept verbatim.

Every file in ``fixtures/`` is one card copied out of
``L3200_структурированный_справочник_CLI_RU.md`` without a character changed.
Hand-written approximations were tried first and were worse than useless: the
damage these tests are about is damage a conversion did to whitespace, and
whitespace is exactly what a person retyping an example quietly repairs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def excerpt():
    def load(name: str) -> str:
        return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")

    return load
