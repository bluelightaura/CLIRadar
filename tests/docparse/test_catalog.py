from __future__ import annotations

import json

from cliradar.docparse import build_catalog, read_card, split_cards


def test_record_carries_the_syntax_as_printed_and_as_read(excerpt) -> None:
    record = read_card(split_cards(excerpt("welded_name"))[0])

    assert record.command == "command authorization aaa method"
    assert record.syntax[0] == "command authorization privilege-level aaa method name"
    assert record.marked[0] == "command authorization <0-15> aaa method <name>"
    assert record.table_read is True
    assert record.takes_no_parameters is False


def test_parameters_report_kind_and_evidence(excerpt) -> None:
    record = read_card(split_cards(excerpt("welded_name"))[0])
    rows = {p.name: p for p in record.parameters}

    assert rows["privilege-level"].kind == "value"
    assert rows["privilege-level"].written == "<0-15>"
    assert rows["privilege-level"].reason == "domain"
    assert "от 0 до 15" in rows["privilege-level"].description


def test_a_card_without_parameters_is_read_not_merely_empty(excerpt) -> None:
    record = read_card(split_cards(excerpt("no_parameters_dash"))[0])

    assert record.takes_no_parameters is True
    assert record.table_read is True
    assert record.parameters == []


def test_catalog_serialises_with_the_manuals_own_alphabet(excerpt) -> None:
    catalog = build_catalog(excerpt("welded_name"), source="excerpt.md")
    payload = json.loads(catalog.to_json())

    assert payload["source"] == "excerpt.md"
    # One card is not a card reference - the catalog is still built, because
    # measuring the near misses is how the threshold gets set.
    assert payload["recognised"] is False
    assert payload["commands"][0]["command"] == "command authorization aaa method"
    assert "привилегий" in catalog.to_json() or "уровень" in catalog.to_json()
