from __future__ import annotations

import pytest

from cliradar.docparse import split_cards
from cliradar.docparse.profile import (
    PROFILES,
    builtin,
    default,
    lexicon_from_dict,
    load,
)

# A manual of the same shape written by another vendor: the heading is a
# section number rather than a backticked name, and the blocks are called
# something else. Nothing about the reading changes - only the names do, which
# is the whole claim the profile makes. The names and the table's header are
# the live document's, not a guess about it - see tests/docparse/test_docx.py,
# which reads three of its pages.
CENTEC_SHAPED = """
2.1.1 auth-degenerate

**Command Format**

```text
auth-degenerate { enable | disable }
```

**Parameter Description**

```text
Parameter Description Values
enable enables switching to local authentication -
```
"""


def test_the_default_profile_is_the_manual_this_reader_was_written_against() -> None:
    assert default().name == "l3200_ru"
    assert default() is builtin("l3200_ru")  # cached, not re-read per document


def test_every_shipped_profile_loads() -> None:
    files = sorted(PROFILES.glob("*.json"))
    assert files, "profiles are package data and must ship with the package"
    for path in files:
        profile = load(path)
        assert profile.name == path.stem
        assert "command" in profile.heading.groupindex


def test_a_second_shape_is_read_by_naming_it_and_nothing_else() -> None:
    assert split_cards(CENTEC_SHAPED) == []  # not this manual's shape

    cards = split_cards(CENTEC_SHAPED, profile=builtin("centec_eng"))

    assert [card.command for card in cards] == ["auth-degenerate"]
    assert cards[0].syntax == ["auth-degenerate { enable | disable }"]
    assert set(cards[0].parameters) == {"enable"}


def test_thresholds_come_from_the_profile_too() -> None:
    assert builtin("centec_eng").min_cards == 20
    with pytest.raises(FileNotFoundError):
        builtin("no_such_manual")


def test_an_ip_address_is_not_a_section_number() -> None:
    """A card heading is a section number and a command; an address is neither.

    Sample output in the Centec manual prints lines like "255.255.255.255
    telnet", which read as a heading opened fifteen phantom cards with no
    blocks in them. Every one of the manual's 1771 real headings numbers three
    levels deep, and everything deeper is an address, so the rule is exact
    rather than a guess about what looks plausible.
    """
    heading = builtin("centec_eng").heading

    assert heading.match("2.1.55 check temporary-password")
    assert heading.match("9.1.51 show stp interface")
    for address in (
        "255.255.255.255 telnet",
        "0.0.0.0 normal",
        "10.1.1.6 vlan 101 NON-DR 81",
        "255.255.255.0 precedence 2 fragment",
    ):
        assert not heading.match(address), address


def test_the_words_evidence_is_worded_in_belong_to_the_document() -> None:
    """The lexicon is data, and neither manual's vocabulary reaches the other's.

    This is the layer the reader got wrong for a while. The ladder that weighs
    a table cell is an algorithm and is the same for every manual of this
    shape, but what counts as a domain is worded in the language the cell was
    printed in. Written into the ladder's own source, the Russian manual's
    wording was all it had, and on the English one 9.5% of rows fell through to
    a guess for want of the English wording of the very same evidence.
    """
    russian = builtin("l3200_ru").lexicon
    english = builtin("centec_eng").lexicon

    assert russian.domain.search("Целое число от 1 до 15")
    assert english.domain.search("in dotted decimal format")
    # Neither manual is asked to recognise the other's phrasing, which is what
    # keeps one document's vocabulary from deciding another document's rows.
    assert not russian.domain.search("in dotted decimal format")
    assert not english.domain.search("Целое число от 1 до 15")


def test_a_profile_that_names_no_vocabulary_guesses_rather_than_answers() -> None:
    # An absent lexicon has to be unmatchable rather than permissive. A
    # profile for a manual nobody has read yet should fall through to guesses,
    # which the measurement counts and reports; a permissive default would
    # answer every question wrongly and quietly.
    silent = lexicon_from_dict({})

    assert not silent.domain.search("Целое число от 1 до 15")
    assert not silent.effect.match("enables the feature")
    assert silent.range_in("with a range of 0~15") is None


def test_one_domain_written_twice_is_one_domain() -> None:
    # The rule "two ranges in one cell, invent neither" is about disagreement.
    # The English manual states one domain in both its notations at once, and
    # that disagrees with nothing.
    english = builtin("centec_eng").lexicon

    assert english.range_in(
        "Autonomous System Number value range in integer form, the range is 1 to "
        "65535 string4 byte value range in integer form, the range is <1-65535>."
    ) == ("1", "65535")
    # Genuinely different bounds still yield nothing: here a neighbouring row's
    # "<0-23>" landed beside this row's own range.
    assert english.range_in(
        "range-number is an integer in the range of 1 to 16. Integer format, "
        "with values ranging from <0-23>:"
    ) is None


def test_a_compound_domain_is_not_collapsed_into_one_of_its_positions() -> None:
    # An interface written "<0-0>/<0-0>/<0-0>" asks for three numbers, and
    # calling it "<0-0>" would claim the device wants one. The repetitions are
    # told from a restatement by what stands between them: prose restates,
    # punctuation compounds. The middle position lost its hyphen in conversion,
    # which is why the test is not for a slash exactly.
    russian = builtin("l3200_ru").lexicon

    assert russian.range_in("Номер интерфейса в формате <0-0>/<00>/<0-0>") is None
