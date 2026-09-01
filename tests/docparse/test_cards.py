from __future__ import annotations

from cliradar.docparse import is_card_reference, read_card, split_cards
from cliradar.docparse.cards import Card, says_no_parameters
from cliradar.docparse.profile import builtin


def test_takes_only_the_syntax_block_as_command_surface(excerpt) -> None:
    card = split_cards(excerpt("welded_header"))[0]

    # The card also carries a purpose paragraph, a default value and a worked
    # example. None of them is a command, and read line by line all three used
    # to arrive as one.
    assert card.command == "clock set"
    assert card.syntax == [
        "clock set HH:MM:SS DD MM YYYY",
        "clock set HH:MM:SS YYYY/MM/DD",
    ]


def test_no_parameters_survives_a_dash_landing_in_the_break(excerpt) -> None:
    # The row reads "— Команда не принимает па- —" and then "раметров": the
    # empty values column puts a dash inside a word the conversion broke. Both
    # have to be undone, and in that order.
    card = split_cards(excerpt("no_parameters_dash"))[0]

    assert card.takes_no_parameters is True
    assert card.had_parameter_block is True
    assert card.parameters == {}


def test_unreadable_table_is_not_mistaken_for_a_command_without_parameters() -> None:
    # The distinction the whole reader rests on. A table that yielded nothing
    # is a failure to be counted, not a card of pure keywords.
    assert says_no_parameters(["Параметр   Описание", "acl-name   Имя списка"]) is False
    assert says_no_parameters(["—   Команда не имеет параметров."]) is True


def test_a_document_must_earn_this_reader(excerpt) -> None:
    one = split_cards(excerpt("welded_header"))
    assert is_card_reference(one) is False  # a handful of headings proves nothing

    enough = [
        Card(command=f"show thing {n}", syntax=[f"show thing {n}"], had_parameter_block=True)
        for n in range(20)
    ]
    assert is_card_reference(enough) is True

    # One card in four of the wrong shape, and the document is handed back to
    # the line reader rather than forced into a shape it does not have.
    mixed = enough + [Card(command=f"prose {n}") for n in range(7)]
    assert is_card_reference(mixed) is False


def test_no_parameters_said_with_an_adjective_in_the_way() -> None:
    # "не требует дополнительных параметров" means what "не имеет параметров"
    # means, and cost this reader a card until it was allowed to.
    assert says_no_parameters(["—  Команда не требует", "дополнительных", "параметров"]) is True


def test_a_command_with_nothing_to_describe_is_read_completely(excerpt) -> None:
    # Every form of "laser bias-current-threshold auto" is its own name, and
    # its parameter block is a sentence about the default behaviour with no
    # table in it. Reading no rows out of that was counted as a table this
    # reader failed on; it is not a failure, and no row it could have carried
    # would have changed a single token of the marked syntax.
    card = split_cards(excerpt("eng_no_parameters_to_have"), profile=builtin("centec_eng"))[0]

    assert card.parameters == {}
    assert card.had_parameter_block is True
    assert card.takes_no_parameters is False  # it never said so
    assert card.nothing_to_describe is True
    assert read_card(card, builtin("centec_eng")).table_read is True


def test_a_card_with_parameters_left_undescribed_is_still_a_failure(excerpt) -> None:
    # The other side of the same question, and the reason it is asked of the
    # syntax rather than of the table: "show ipv6 ospf log lsa | nbr | spf"
    # does have tokens a table could be about, so a block that named none of
    # them is a table this reader could not read, and must be counted as one.
    card = Card(
        command="show ipv6 ospf log",
        syntax=["show ipv6 ospf log", "show ipv6 ospf log lsa"],
        had_parameter_block=True,
    )

    assert card.nothing_to_describe is False
    assert read_card(card, builtin("centec_eng")).table_read is False


def test_a_heading_carrying_no_block_is_not_a_card() -> None:
    """A line can look like a heading without one standing there.

    The Centec manual illustrates its own typographic conventions in the front
    matter by printing three sample card headings. Read literally they open
    three cards that carry nothing, while the commands they name have real
    cards further down - so the catalog gained three empty entries and the
    measurement counted three reads it never made.

    Declining them is not the same as hiding a failed read, and the rule is the
    document's rather than a guess: of 1771 headings in that manual exactly
    those 3 carry no block, and of 1795 in the L3200 reference none do.
    """
    profile = builtin("centec_eng")
    text = """Format Meaning
8.3.9 filter action redirect
8.9.35 show dot1x statistic
2.1.1 auth-degenerate
**Command Format**
```
auth-degenerate { enable | disable }
```
"""

    cards = split_cards(text, profile)

    assert [card.command for card in cards] == ["auth-degenerate"]


def test_a_card_whose_block_titles_were_never_translated_is_still_read() -> None:
    """The Centec manual is translated as far as its block titles and no further.

    Seven of its cards still head their blocks in Chinese. The syntax is
    printed under them exactly as everywhere else, so the card was not damaged
    - it was unreadable only because the title above it was not on the list.
    Which words title a block is the document's property and lives in the
    profile, so this is a change of data rather than of code.
    """
    profile = builtin("centec_eng")
    text = """10.5.2 patch load
**命令形式**
```
patch patch-number load file-name
```
**参数说明**
```
参数 说明 取值
patch-number 指定补丁号 1~100
```
"""

    card = split_cards(text, profile)[0]

    assert card.syntax == ["patch patch-number load file-name"]
    assert card.had_parameter_block is True
    assert "patch-number" in card.parameters


def test_keeps_the_purpose_block_even_though_it_is_not_fenced(excerpt) -> None:
    # The two listing blocks are fenced and the purpose block is not, so a
    # reader that only looks inside fences drops it - which is why every entry
    # of the catalog used to carry an empty description. See defect 4 in
    # docs/DOCPARSE_DEFECTS_RU.md.
    card = split_cards(excerpt("welded_header"), builtin("l3200_ru"))[0]

    assert card.purpose == [
        (
            "clock set Команда может быть использована для установки текущей"
            " даты и времени коммутатора."
        )
    ]


def test_purpose_keeps_one_line_per_form_of_the_command(excerpt) -> None:
    card = split_cards(excerpt("purpose_per_form"), builtin("l3200_ru"))[0]

    assert len(card.purpose) == 2
    assert card.purpose[0].startswith("clock timezone Команды")
    assert card.purpose[1].startswith("no clock timezone Эта команда")
