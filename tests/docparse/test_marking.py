from __future__ import annotations

from cliradar.docparse import mark_card, mark_parameters, placeholder, split_cards
from cliradar.docparse.profile import builtin


def test_domain_becomes_the_placeholder_the_device_prints(excerpt) -> None:
    # "числа со значениями от 0 до 15" is the same domain the switch's own help
    # emits as <0-15>. Written any other way, a compare reports a command the
    # device really has as missing.
    card = split_cards(excerpt("welded_name"))[0]

    assert mark_parameters(card) == [
        "command authorization <0-15> aaa method <name>",
        "no command authorization <0-15> aaa method",
    ]


def test_keywords_are_left_exactly_as_the_manual_prints_them(excerpt) -> None:
    card = split_cards(excerpt("no_parameters_dash"))[0]

    # A card that says it has no parameters is read completely: every token of
    # it is typed verbatim.
    assert mark_parameters(card) == ["show ip config"]


def test_every_decision_carries_the_evidence_that_settled_it(excerpt) -> None:
    card = split_cards(excerpt("welded_name"))[0]
    _, marks = mark_card(card)
    by_token = {mark.token: mark for mark in marks}

    # The heading says "command authorization aaa method", so the words of the
    # name are settled before the table is consulted at all.
    assert by_token["command"].reason == "command-name"
    # "aaa" comes after privilege-level, so the heading no longer vouches for
    # it - it is a keyword because the table never mentions it.
    assert by_token["aaa"].reason == "untabled"
    assert by_token["privilege-level"].reason == "domain"
    assert by_token["privilege-level"].kind == "value"
    assert by_token["command"].kind == "keyword"


def test_two_ranges_in_one_cell_yield_no_range() -> None:
    # A per-model domain. Inventing one of the two would be a claim the manual
    # does not make, so the name is used - true for every model.
    assert placeholder("port", "от 1 до 24, от 1 до 48") == "<port>"
    assert placeholder("port", "Целое число от 1 до 24") == "<1-24>"


def test_the_english_manual_writes_a_range_with_a_tilde() -> None:
    # Both sentences are copied from the Centec reference. The notation is that
    # manual's own: written with a hyphen a range would be indistinguishable
    # from a hyphenated name.
    assert (
        placeholder("level-value", "specifies the integer form of the command level, "
                    "with a range of 0~15.")
        == "<0-15>"
    )
    assert (
        placeholder("t", "Repeat sending ICMP ECHO packets Value range is 1~4294967295, "
                    "default value is 5")
        == "<1-4294967295>"
    )
    # A tilde is also how that manual quotes the character itself, in the list
    # of what a file name may not contain, and two of them are not a range.
    assert placeholder("file-name", 'Cannot be spaces, "~", "*", "/"') == "<file-name>"
    assert placeholder("port", "ports 1~8 and vlans 1~4094") == "<port>"


def test_bare_alternative_keeps_its_keywords(excerpt) -> None:
    # The manual prints "enable password level level-value cipher | plain
    # password" - a choice, but with no braces around it. "cipher", "plain" and
    # the trailing "password" are typed verbatim; only level-value is supplied.
    card = split_cards(excerpt("bare_alternative"))[0]

    assert mark_parameters(card)[0] == "enable password level <0-15> cipher | plain <password>"


def test_the_ladder_weighs_the_manual_the_card_came_from(excerpt) -> None:
    # The same rung, the same evidence, two documents' wording. Read with the
    # other manual's profile the row is settled by nothing and guessed at, and
    # the reason recorded says so - which is how the measurement found this.
    card = split_cards(excerpt("eng_capitalised_name"), profile=builtin("centec_eng"))[0]

    _, marks = mark_card(card, builtin("centec_eng"))
    by_token = {mark.token: mark for mark in marks}
    assert by_token["isis-instance"].reason == "domain"
    assert by_token["isis-instance"].text == "<1-65535>"

    _, foreign = mark_card(card, builtin("l3200_ru"))
    assert {mark.token: mark for mark in foreign}["isis-instance"].reason == "default"


def test_an_effect_is_only_read_at_the_head_of_the_cell(excerpt) -> None:
    # The order of the last two rungs was measured, not chosen. Asking the
    # effect first reads "vty-number configures the terminal number" as a
    # keyword, and the English manual has thirty-five rows like it against
    # twenty-six it would rescue; on the Russian one it turns "queue-index
    # Указывает индекс очереди Целое число от 0 до 7" into a keyword too. A
    # domain stated anywhere in the cell outranks a verb standing at its head.
    english = builtin("centec_eng").lexicon

    assert english.effect.match("configures the terminal number")
    assert english.domain.search("configures the terminal number integer value")
