"""Reading the second manual, which arrived as a .docx made from a PDF.

The fixture is not a description of that file's structure - it is three of its
pages, cut out with their paragraphs, their nesting and their w:ind attributes
untouched. That matters more here than anywhere else in this package: what has
to be undone is what the conversion did to the *structure*, and a hand-written
Word document would have the structure a person thought it had.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cliradar.docparse import docx, split_cards
from cliradar.docparse.profile import builtin

FIXTURE = Path(__file__).parent / "fixtures" / "centec_pages.docx"


@pytest.fixture
def profile():
    return builtin("centec_eng")


@pytest.fixture
def text(profile):
    return docx.read(FIXTURE, profile)


@pytest.fixture
def cards(text, profile):
    return {card.command: card for card in split_cards(text, profile)}


def test_both_layers_are_read_because_neither_holds_the_whole_manual(cards) -> None:
    # "configure" is printed in the layer whose paragraphs carry no offsets and
    # "command-privilege level view" in the layer whose paragraphs do. Reading
    # either alone loses half the document.
    assert "configure" in cards
    assert "command-privilege level view" in cards


def test_a_card_is_assembled_in_printed_order_not_file_order(text) -> None:
    """The parameter table of 2.1.6 is printed before its heading in the file.

    This is the whole reason ``reading_order`` exists. Left as the file has it,
    the rows describing ``level-value`` land on the card above, which is a
    quiet, plausible, entirely wrong catalog entry for both commands.
    """
    heading = text.index("2.1.6 command-privilege level view")
    rows = text.index("level-value specifies the integer form")
    assert heading < rows
    assert text.index("2.1.5 configure") < heading


def test_the_positioned_layer_is_a_page_behind() -> None:
    """The rule itself, on intervals small enough to read at a glance."""
    intervals = [
        (["flow of page one"], [["positioned before any page"]]),
        (["flow of page two"], [["positioned of page one"]]),
        (["flow of page three"], [["positioned of page two"]]),
    ]

    assert list(docx.reading_order(intervals)) == [
        "positioned before any page",
        "positioned of page one",
        "flow of page one",
        "positioned of page two",
        "flow of page two",
        "flow of page three",
    ]


def test_an_interval_holding_two_pages_does_not_shift_the_rest() -> None:
    """One interval sometimes carries two pages, and the count must not slip.

    Where it did, the whole document after it read a page out of step, and a
    page out of step hands one card's syntax to the card below it - "tftp get"
    printed its four forms inside the card for "tftp put". Pages wait in a
    queue rather than being taken one per interval.
    """
    intervals = [
        (["flow one"], [["positioned one"], ["positioned two"]]),
        (["flow two"], []),
        (["flow three"], [["positioned three"]]),
    ]

    assert list(docx.reading_order(intervals)) == [
        "positioned one",
        "positioned two",
        "flow one",
        "positioned three",
        "flow two",
        "flow three",
    ]


def test_a_page_is_cut_where_its_own_header_stands_again() -> None:
    # Two pages in one chunk, and no list of header wordings to find the seam
    # with: the chunk names its header on the first line, and the line standing
    # again is the next page beginning.
    chunk = [
        "Chapter 2 Basic Commands",
        "2.5.1 tftp get",
        "Chapter 2 Basic Commands",
        "Default Value",
        "None",
    ]

    assert docx._positioned_pages(chunk) == [
        ["2.5.1 tftp get"],
        ["Default Value", "None"],
    ]
    assert docx._positioned_pages([]) == []


def test_a_command_form_broken_across_printed_lines_is_one_command(cards) -> None:
    """One form of this command runs to eight printed lines.

    Left as printed, seven of them arrive in the catalog as commands of their
    own - "mcast | bgp-af-ipv4-vpn | ..." - and the eighth is a truncation of
    the real one. A line is joined where the notation shows the line above it
    was cut short: after a hyphen inside a name, after or before the bar
    between alternatives.
    """
    forms = cards["command-privilege level view"].syntax

    assert len(forms) == 2
    assert forms[0].startswith("command-privilege level level-value view {")
    assert forms[1].startswith("no command-privilege level level-value view {")
    assert all(form.endswith("[ .COMMAND ]") for form in forms)
    # The break fell inside a hyphenated name, and the hyphen it broke on is
    # part of the name rather than a mark of the break.
    assert "bgp-af-ipv4-mcast" in forms[0]
    assert "bgp-af-ipv4mcast" not in forms[0]


def test_a_block_title_broken_across_lines_is_still_a_title(text, cards) -> None:
    """The parameter block is titled "Parameter" on one line, "Description" on the next.

    And the table below it is headed "Parameter Description Values", which must
    not be read as the same title: matching is exact, so the longer line stays
    content and goes on to fix the table's columns.
    """
    assert "**Parameter Description**" in text
    assert "**Parameter Description Values**" not in text
    assert set(cards["command-privilege level view"].parameters) == {
        "level-value",
        "COMMAND",
    }


def test_a_card_saying_None_takes_no_parameters(cards) -> None:
    card = cards["configure"]

    assert card.had_parameter_block
    assert card.takes_no_parameters
    assert not card.parameters


def test_the_syntax_block_is_read_under_both_names_the_manual_uses(cards) -> None:
    # The manual heads the block "Command Format" on most cards and "Command
    # Form" on some, and a card whose block is not recognised has no commands.
    assert cards["configure"].syntax == ["configure"]


def test_page_furniture_and_the_contents_are_not_content(profile) -> None:
    source = [
        "Chapter 2 Basic Commands",
        "2.1.6 command-privilege level view.....................................5",
        "2.1.6 command-privilege level view",
        "Command Format",
    ]

    assert docx.strip_furniture(source, profile) == [
        "2.1.6 command-privilege level view",
        "Command Format",
    ]


def test_the_document_earns_this_reader_the_same_way_any_other_does(text, profile) -> None:
    from cliradar.docparse import is_card_reference

    cards = split_cards(text, profile)

    # Every card these three pages carry whole is read whole. The tail of the
    # card the excerpt opens in the middle of belongs to no heading and is
    # dropped, which is the same thing that happens to the manual's front
    # matter and is what keeps prose out of the catalog.
    assert [card.command for card in cards] == [
        "configure",
        "command-privilege level view",
        "debug cli",
    ]
    assert all(card.structured for card in cards)
    # Three pages is far short of the twenty cards a profile asks for, and the
    # gate says so rather than being talked round by a good-looking excerpt.
    assert not is_card_reference(cards, profile)


# Every line below is copied out of the vendor .docx as this reader hands it
# on, one printed line per entry. The point of each test is what the reader
# must do with lines it did not choose.


def test_prose_inside_the_syntax_block_is_not_welded_onto_a_command() -> None:
    """The manual prints notes inside Command Format, and the card below has two.

    An earlier joiner glued every line that did not open a form, which is how
    "send message This command can be used to send messages from the current
    operating terminal..." reached the catalog as a command.
    """
    block = [
        "send message data",
        "send message",
        (
            "This command can be used to send messages from the current "
            "operating terminal to all other logged-in operating terminals."
        ),
        (
            "The data command can be sent by simply pressing Enter; when using "
            "the send message command, after pressing Enter and"
        ),
        "the Ctrl+Z key to send the message.",
    ]

    assert docx._forms(block, "send message") == ["send message data", "send message"]


def test_a_table_cell_that_drifted_into_the_syntax_block_is_dropped() -> None:
    # Two lines of the description of "simple" landed above the table, inside
    # the block listing the forms. Neither opens a form and neither continues
    # the line above, so neither is one.
    block = [
        "password pass-word",
        "password pass-word simple",
        "reversible algorithm.",
        "Otherwise, it is encrypted using the MD5",
    ]

    assert docx._forms(block, "password") == [
        "password pass-word",
        "password pass-word simple",
    ]


def test_a_heading_naming_two_commands_still_opens_forms() -> None:
    # The card is headed "ping|ping6". Taken as one word, "ping mac-address"
    # reads as a continuation and nine forms weld into one.
    block = [
        "ping mac-address",
        "ping mac-address { -n | -l | -w | -v } VALUE1",
        "ping mac-address -t",
        "ping ipv4-address string1",
        "ping ipv6-address string2",
    ]

    assert docx._forms(block, "ping|ping6") == block


def test_a_form_that_outran_its_printed_line_is_rejoined() -> None:
    # The break falls after the hyphen inside a name, after the bar between
    # alternatives, or just before one - and the hyphen it broke on belongs to
    # the name, so that join closes up without a space.
    block = [
        "command-privilege level level-value view { configure | bgp-af-ipv4-",
        "mcast | bgp-af-ipv6-vpn",
        "| bgp-af-vpnv4 | vlan } [ .COMMAND ]",
    ]

    assert docx._forms(block, "command-privilege level view") == [
        (
            "command-privilege level level-value view { configure "
            "| bgp-af-ipv4-mcast | bgp-af-ipv6-vpn | bgp-af-vpnv4 "
            "| vlan } [ .COMMAND ]"
        )
    ]


def test_the_running_header_is_found_by_where_it_sits_not_by_its_wording() -> None:
    """This manual runs its header in three wordings, one of them Chinese.

    So it is not recognised by wording at all: the first positioned line of a
    page is the header. Checked before being relied on - 1560 of the 1562
    intervals that have a positioned half begin with one.
    """
    footer = builtin("centec_eng").page_footer
    paragraphs = [
        (True, "Chapter 5 Routing Commands"),
        (False, "ntp broadcast-server version { 1 | 2 | 3 | 4 }"),
        (True, "Switch(config)#"),
        (False, "Switch Command Line Manual 42"),
        (True, "管理命令"),
        (False, "show snmp user"),
        (False, "Switch Command Line Manual 43"),
    ]

    assert list(docx._intervals(paragraphs, footer)) == [
        (["ntp broadcast-server version { 1 | 2 | 3 | 4 }"], [["Switch(config)#"]]),
        (["show snmp user"], [[]]),
    ]


def test_an_optional_negation_written_on_the_form_still_opens_it() -> None:
    """The manual marks the negated form on the form itself: "[no] iss master".

    A prefix test on the first word sees "[no]" and no opener matches it, so
    the card loses its whole syntax block - which is what happened to all three
    stack cards. The space after the bracket is not reliable either: one of
    them prints "[no]join stack-port 1" closed up.
    """
    assert docx._forms(["[no] iss master"], "iss master") == ["[no] iss master"]
    assert docx._forms(["[no]join stack-port 1"], "join stack-port") == [
        "[no] join stack-port 1"
    ]


def test_a_form_the_manual_set_with_a_capital_is_still_a_form() -> None:
    # "Interface stack-port 1" under a card headed "interface stack-port". The
    # heading is authoritative for the spelling, so the letter is put back down
    # - the catalog holds what the operator types.
    assert docx._forms(["Interface stack-port 1"], "interface stack-port") == [
        "interface stack-port 1"
    ]


def test_a_note_is_not_a_form_of_every_command() -> None:
    """``no`` is matched whole, not as a prefix.

    Every card offers ``no`` as an opener, and matched by prefix it also opens
    on "Note:", "Nothing" and "Non-specified" - and worse, on the continuation
    line "november | december } ..." of a wrapped alternative, which reached
    the catalog as a command of its own.
    """
    assert docx._forms(["Note:", "note: see above"], "ftp6 get") == []
    assert docx._forms(
        ["november | december } start-hour:start-minutes"], "clock summer-time"
    ) == []


def test_prose_the_manual_set_in_title_case_is_not_a_form() -> None:
    # Forgiving the capital is only safe where the rest of the line is a
    # command. This one is a sentence about the command, and lowering its first
    # letter alone would make it a form of it.
    block = ["Show IGMP-Snooping Statistic Interface Eth-Trunk Trunk-Number"]
    assert docx._forms(block, "show igmp-snooping statistic") == []
