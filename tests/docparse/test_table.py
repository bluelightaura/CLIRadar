from __future__ import annotations

import pytest

from cliradar.docparse import parameter_rows, split_cards
from cliradar.docparse.profile import builtin
from cliradar.docparse.table import _table_body, _vocabulary


def test_reads_a_table_whose_header_words_were_welded_together(excerpt) -> None:
    # The header prints as "ПараПримечание                  Значение" - the
    # conversion ate the gutter between the first two titles. A reader that
    # demanded two spaces after "Параметр" lost 207 cards of this manual to
    # exactly this line.
    card = split_cards(excerpt("welded_header"))[0]

    assert set(card.parameters) == {"HH", "MM", "SS", "DD", "YYYY"}


def test_header_is_recognised_by_its_first_word_alone(excerpt) -> None:
    body, indent, edge = _table_body(["ПараПримечание                  Значение", "  HH   Часы"])
    assert body == ["  HH   Часы"]
    assert indent == 0
    assert edge is not None


def test_recovers_a_name_the_conversion_welded_into_the_prose(excerpt) -> None:
    # The name column holds "в виprivilege-" and the line below holds "level":
    # the words of two columns were spliced mid-syllable and the name broken
    # across the join. It is recovered because the card's own syntax spells
    # "privilege-level" out, and nothing else in that wreckage does.
    card = split_cards(excerpt("welded_name"))[0]

    assert set(card.parameters) == {"privilege-level", "name"}


def test_row_keeps_the_text_of_both_right_hand_columns(excerpt) -> None:
    card = split_cards(excerpt("welded_name"))[0]
    row = card.parameters["privilege-level"]

    # Description and values are read as one body of text, so the row carries
    # both what the parameter does ("Укажите уровень разрешения") and the
    # domain it admits ("целого числа со значениями от 0 до 15"), whichever
    # column the conversion threw the words into.
    assert "Укажите уровень разрешения" in row
    assert "от 0 до 15" in row


@pytest.mark.xfail(
    strict=True,
    reason="vertical drift: the values cell of a row can be set above the name it "
    "belongs to, and is then given to the row before it",
)
def test_row_keeps_its_own_values_cell(excerpt) -> None:
    # The table prints "Название метода авториза- | Формат строки, максимальная"
    # on the line ABOVE "name", and both go to privilege-level instead. The
    # domain of "name" is lost and a domain it does not have is added to the
    # row above. Counted in the measure as a parameter settled by the weakest
    # rule - see docs/DOC_PARSING_RU.md.
    card = split_cards(excerpt("welded_name"))[0]

    assert "Формат строки" in card.parameters["name"]
    assert "Формат строки" not in card.parameters["privilege-level"]


def test_a_block_with_no_header_yields_nothing() -> None:
    # The intended failure. Every token of the card then reads as a keyword,
    # which is how the line reader behaved before this package existed.
    assert parameter_rows(["acl-name   Имя списка доступа"], ["ip access-list acl-name"]) == {}


def test_rows_are_anchored_on_the_syntax_and_not_on_whitespace() -> None:
    lines = [
        "Параметр  Примечание  Значение",
        "privilege-value Уровень разрешений пользователя  Целое число от 0 до 15",
    ]
    # A single space between the name and its description, because the name
    # fills its column. Splitting on a run of two spaces made the whole line
    # into a parameter no device has.
    # The row keeps its whole line bar the name, which is already the key it
    # is filed under - what matters is that the key is the parameter and not
    # the sentence that followed it.
    assert parameter_rows(lines, ["privilege privilege-value"]) == {
        "privilege-value": "Уровень разрешений пользователя Целое число от 0 до 15"
    }

    # The same table read without the syntax to lean on: no anchor, no rows.
    assert parameter_rows(lines, []) == {}


def test_shape_is_consulted_only_when_the_syntax_vouches_for_nothing() -> None:
    lines = [
        "Параметр      Описание              Допустимые значения",
        "module-name   Имя модуля системного ПО   См. список выше",
    ]
    # The card enumerates the parameter's values where its name should be, so
    # the syntax cannot vouch for "module-name" - and without a last resort the
    # whole table is lost.
    assert parameter_rows(lines, ["show logging source { aaa | acl }"]) == {
        "module-name": "Имя модуля системного ПО См. список выше"
    }


def test_shape_never_overrules_the_syntax() -> None:
    lines = [
        "Параметр      Описание              Допустимые значения",
        "vlan-id       Идентификатор VLAN    Целое число от 1 до 4094",
        "мусор         строка от съехавшего  описания",
    ]
    # With an anchor to stand on, only the anchored row is opened: the second
    # line is wreckage, and geometry would have taken it for a row.
    assert set(parameter_rows(lines, ["igmp group vlan vlan-id"])) == {"vlan-id"}


def test_a_row_may_open_with_a_capital(excerpt) -> None:
    # "Isis-instance is the integer form of the ISIS instance number" - the
    # manual capitalises the word that opens a row, the syntax prints
    # "isis-instance", and a case-sensitive lookup lost the only row on the
    # card. What goes into the catalog is the syntax's spelling: that is what
    # the operator types.
    card = split_cards(excerpt("eng_capitalised_name"), profile=builtin("centec_eng"))[0]

    assert set(card.parameters) == {"isis-instance"}


def test_only_the_capital_that_opens_a_row_is_forgiven() -> None:
    # A description too long for its column wraps into the name column, and
    # what stands there is often the feature's own name as the prose writes it.
    # Folding case wholesale gives "MAC-VLAN" a row of its own, which then
    # takes the text belonging to the parameter below it.
    rows = parameter_rows(
        [
            "Параметр      Описание              Допустимые значения",
            "vlan-id       Идентификатор VLAN    Целое число от 1 до 4094",
            "              MAC-VLAN привязка",
        ],
        ["mac-vlan priority mac-address vlan-id priority"],
    )

    assert set(rows) == {"vlan-id"}


def test_a_name_the_column_split_into_two_words(excerpt) -> None:
    # The name column is too narrow for "process-id", so the table prints
    # "Process ID is an integer value, with a range of 1 to 256". Opening the
    # row on "process" would name it after a keyword the same card uses and
    # hand this row's domain to it.
    card = split_cards(excerpt("eng_name_split_in_two"), profile=builtin("centec_eng"))[0]

    assert set(card.parameters) == {"process-id"}
    assert "1 to 256" in card.parameters["process-id"]


def test_two_words_are_joined_only_across_a_single_space() -> None:
    # "groupnum   Идентификатор группы   Целое число от 1 до 4" would otherwise
    # join its name to the "1" of its own domain and open as "groupnum1" - a
    # name this card's syntax really does use, on another form, which is what
    # let the mistake survive every check downstream.
    rows = parameter_rows(
        ["Параметр   Описание             Допустимые значения",
         "groupnum   Идентификатор группы Целое число от 1 до 4"],
        ["mirror group groupnum eth-trunk trunk-number", "no mirror group [groupnum1 ]"],
    )

    assert set(rows) == {"groupnum"}


def test_recovers_a_name_its_own_description_was_welded_to(excerpt) -> None:
    # The gutter itself vanished: every row of this card arrives as one word
    # followed by its description - "sendIGMP Protocol Send Message Debug
    # Information". The name is a prefix, and the card's syntax says which.
    card = split_cards(excerpt("eng_welded_head"), profile=builtin("centec_eng"))[0]

    assert {"send", "receive", "protocol", "device", "event", "timer", "all"} <= set(
        card.parameters
    )


def test_a_welded_head_is_only_read_where_a_capital_follows() -> None:
    # The weld happened at a gutter, and across a gutter stands the
    # description, which this manual begins with a capital. An ordinary word
    # that merely starts with a parameter's name continues in lower case.
    known = ["debug igmp { send | receive } vpn-instance"]

    assert set(parameter_rows(
        ["Parameter Description Values", "sendIGMP Protocol Send Message Debug Information -"],
        known,
    )) == {"send"}
    assert "send" not in parameter_rows(
        ["Parameter Description Values", "sendmessage queue depth exceeded -"], known
    )


def test_two_spellings_of_one_length_fold_the_same_way_every_run() -> None:
    """The fold has to be a function of the card, not of the process.

    "show mac-address MAC-ADDRESS" prints both spellings of one name, they are
    the same length, and they fold to the same key. Ordered by length alone the
    winner came out of set iteration - that is, out of the hash seed - and the
    catalog then differed between runs of the same reader on the same file.
    Diffing two catalogs is how every change to this package is checked, so a
    catalog that moves on its own is worse than a catalog that is wrong in a
    fixed way.

    Ties go to the spelling that sorts first, and on the one card where this
    decides anything that is also the right reading: taking the other splits
    the row describing "security" in two and gives half of it to a name the
    table never described.
    """
    syntax = ["show mac-address", "show mac-address MAC-ADDRESS"]

    assert _vocabulary(syntax).folded["mac-address"] == "MAC-ADDRESS"


def test_drops_the_tail_of_a_header_word_the_column_broke(excerpt) -> None:
    # The header reads "ПараПримечание" over "метр": the name column was too
    # narrow for "Параметр". The orphaned "метр" stood as the table's first
    # body line, above the first row, and was handed to that row's text - 53
    # of this manual's tables opened a description with "метр" or "значения".
    # Defect 4 in docs/DOCPARSE_DEFECTS_RU.md.
    card = split_cards(excerpt("purpose_per_form"), builtin("l3200_ru"))[0]

    assert card.command == "clock timezone"
    first = next(iter(card.parameters.values()))
    assert not first.startswith("метр")
    assert not any(text.startswith("метр ") for text in card.parameters.values())


def test_the_row_does_not_repeat_the_name_it_is_filed_under() -> None:
    lines = [
        "Параметр   Описание                            Значение",
        "offset     Укажите разницу во времени от UTC   В формате HH:MM",
    ]
    # The name opening the row is the key the text is filed under, and leaving
    # it in the text is what made 99.9% of the PDF-derived manual's rows look
    # like the corruption the hand-made one really has.
    assert parameter_rows(lines, ["clock timezone time-zone-name add offset"]) == {
        "offset": "Укажите разницу во времени от UTC В формате HH:MM"
    }


def test_a_flag_row_answers_to_the_name_its_syntax_spells() -> None:
    lines = [
        "Параметр   Описание",
        "-t         Повторная передача пакетов ICMP ECHO",
    ]
    # The syntax prints "-t" and the reader keeps "t"; the row prints the dash.
    # Comparing without hyphens is what lets the row shed its own name here -
    # the only place in either manual where the spellings differ at all.
    assert parameter_rows(lines, ["ping mac-address -t"]) == {
        "t": "Повторная передача пакетов ICMP ECHO"
    }


def test_the_name_is_taken_only_off_the_head() -> None:
    lines = [
        "Параметр    Описание",
        "acl-number  Номер списка; acl-number совпадает с номером выше",
    ]
    # A description that goes on to mention the parameter keeps that mention:
    # only the word standing where the row opens is the redundant one.
    assert parameter_rows(lines, ["ip access-group acl-number"]) == {
        "acl-number": "Номер списка; acl-number совпадает с номером выше"
    }


def test_the_name_is_not_eaten_out_of_a_longer_word() -> None:
    # Taking the name off used to carry no word boundary, so "disable" came out
    # of the head of "disables" and the row was left as "s the IS-IS graceful
    # restart feature" - text describing no effect and no domain, which shipped
    # a plainly literal choice as the placeholder <disable>.
    lines = [
        "Parameter Description Values",
        "disable disables the IS-IS graceful restart feature -",
    ]

    assert parameter_rows(lines, ["graceful-restart disable"]) == {
        "disable": "disables the IS-IS graceful restart feature -"
    }


def test_a_name_welded_to_its_description_still_comes_off() -> None:
    # The other side of that boundary, from the hand-made Russian manual: the
    # column edge closed on "simple-password" and the description follows with
    # no space at all. A boundary that refused every letter would leave the
    # whole weld standing and the row would describe nothing. A Cyrillic letter
    # cannot be continuing a Latin name, so it is read as the weld it is.
    lines = [
        "Параметр   Описание",
        "simple-passwordВключение простой (текстовой) аутентификации",
    ]

    assert parameter_rows(lines, ["ip ospf authentication simple-password <1-8>"]) == {
        "simple-password": "Включение простой (текстовой) аутентификации"
    }


def test_a_row_spending_both_columns_on_its_name_says_nothing() -> None:
    # Centec writes "arp ARP-" and "index index -": the name column and the
    # description column hold the same word. The repeat is silence, not prose,
    # and the row has to come out empty - that is what tells the ladder the
    # token is a literal keyword rather than a value it had to guess at.
    assert parameter_rows(["Parameter Description", "arp ARP-"], ["show hwroute hardware arp"]) == {
        "arp": ""
    }


def test_a_repeat_with_something_after_it_is_a_real_word() -> None:
    # The other half of the same rule. "password Password in string form"
    # describes a password; taking the noun as well left "in string form".
    lines = ["Parameter Description Values", "password Password in string form"]

    assert parameter_rows(lines, ["enable password { cipher | plain } password"]) == {
        "password": "Password in string form"
    }
