from __future__ import annotations

import json

from cliradar.docparse import build_catalog, purpose_for, read_card, split_cards
from cliradar.docparse.profile import builtin


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


def test_purpose_is_picked_for_the_form_it_was_written_about(excerpt) -> None:
    # The card carries one bullet per form. Handing "no clock timezone" the
    # bullet about "clock timezone" would describe the opposite of what the
    # command does.
    card = split_cards(excerpt("purpose_per_form"), builtin("l3200_ru"))[0]

    positive = purpose_for(card, "clock timezone time-zone-name add offset")
    negative = purpose_for(card, "no clock timezone")

    assert positive.startswith("Команды могут использоваться")
    assert negative.startswith("Эта команда может быть использована для сброса")
    # The command's own words open the bullet and are already the catalog key.
    assert "clock timezone" not in positive


def test_purpose_falls_back_to_the_whole_block(excerpt) -> None:
    card = split_cards(excerpt("purpose_per_form"), builtin("l3200_ru"))[0]

    # A command the block says nothing about gets the block, not silence.
    assert purpose_for(card, "show running-config").startswith("clock timezone Команды")
    assert purpose_for(card).startswith("clock timezone Команды")


def test_record_carries_the_card_description(excerpt) -> None:
    card = split_cards(excerpt("welded_header"), builtin("l3200_ru"))[0]

    assert read_card(card, builtin("l3200_ru")).description.startswith("clock set Команда")
