"""What varies between manuals, kept as data instead of as code.

Two documents of the same shape disagree about almost nothing that matters and
about everything that is written down: one heads a card with ``#### `command` ``
and another with ``2.1.1 command``; one calls the syntax block **Синтаксис**
and another **Command Format**; one titles the table's first column *Параметр*
and another *Parameter*. None of that is an algorithm. It is a list of names,
and a list of names belongs in a file a person can edit without touching the
reader.

The names are not the only thing a manual owns. It owns the words it explains
a parameter with, too: one says "Целое число от 1 до 15" where another says
"with a range of 0~15", one says "Включить" where another says "enables". Those
words are evidence the marking ladder weighs, and they are as much this
document's property as the title of its syntax block - so they live here as a
``Lexicon`` rather than in the ladder's own source. Keeping them in the code
had a measurable cost: the ladder was written against the Russian manual, and
on the English one it fell through to a guess on 9.5% of rows because the
English wording of the very same evidence was nowhere in it. Written per
document, neither manual's vocabulary can reach the other's rows.

What is emphatically NOT here is the repair - anchoring rows against the card's
own syntax, mending a name broken across a column edge, undoing a word welded
into the prose beside it - nor the ladder that decides which piece of evidence
outranks which. That is an algorithm with state, it is the same for every
manual of this shape, and expressing it as configuration would produce a
language harder to read than the code it replaced.

A profile is loaded from JSON so that adding a manual means adding a file:

    from cliradar.docparse.profile import load, builtin
    profile = builtin("l3200_ru")
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from itertools import pairwise
from pathlib import Path

PROFILES = Path(__file__).parent / "profiles"


@dataclass(frozen=True)
class Lexicon:
    """The words one manual explains a parameter with.

    ``marking`` asks three questions of a table cell - does it state a domain of
    values, is that domain an address, does it describe an effect - and the
    order it asks them in is an algorithm. What counts as a yes is not: it is
    the vocabulary of one document, and the two manuals this reader has seen
    share almost none of it.

    ``ranges`` is a list rather than one pattern because a manual writes a
    domain in more than one notation ("от 1 до 24", "<0-15>", "0~15"), and each
    notation is a separate small pattern with two groups - the low bound and
    the high one - instead of one pattern with eight. The list is ordered:
    where two notations could read the same characters, the first one listed
    takes them.
    """

    # A cell that states a domain of values rather than an effect. The
    # strongest evidence a row names a parameter and not an enumerated keyword.
    domain: re.Pattern[str]
    # A domain that is an address rather than a count. Kept apart because the
    # placeholder is built differently: no range is invented, whatever numbers
    # stand near it - "<0-255>" for an IP address claims the device asks for
    # one octet, which is worse than admitting no domain at all.
    address: re.Pattern[str]
    # A cell describing what the token does to the device, which means the
    # operator types it verbatim. Matched at the start of the cell, not
    # searched: this manual's cells collect the neighbouring row's text at
    # their tail, and only the opening words are reliably about this row.
    effect: re.Pattern[str]
    # Each notation the manual writes a numeric domain in. Two groups apiece.
    ranges: tuple[re.Pattern[str], ...] = ()

    def range_in(self, description: str) -> tuple[str, str] | None:
        """The one numeric domain this cell states, or None if it states none.

        None is also the answer when it states two. A cell carrying two ranges
        is a per-model domain ("от 1 до 24, от 1 до 48"), and picking one of
        them would be a claim the manual does not make.

        Two is counted by what the ranges say, not by how many times they are
        written. The rule is about disagreement, and the English manual states
        one domain twice - "the range is 1 to 65535 ... <1-65535>" - which
        disagrees with nothing. Where the bounds really differ, as when a
        neighbouring row's "<0-23>" lands beside this row's "range of 1 to 16",
        the cell states two and gets no range at all.

        A run of ranges joined by slashes is neither one domain nor two: it is
        one compound domain, and an interface written "<0-0>/<0-0>/<0-0>" asks
        for three numbers. Collapsing it to "<0-0>" would claim the device
        wants one, so the cell gets no range - the same answer it got before
        repetitions were collapsed at all. The two cases are told apart by what
        stands between the repetitions: a manual restating a domain puts prose
        between the statements, and a compound puts punctuation. That is what
        the test asks, rather than asking for a slash exactly, because the
        conversion damages the run itself - one manual prints the middle
        position as "<00>", its hyphen gone.

        Overlapping notations are counted once for the same reason at the level
        below: a span already taken by an earlier pattern is not offered to a
        later one, so "<0-15>" is one range and not also a hyphenated pair.
        """
        found: list[tuple[int, int, str, str]] = []
        for pattern in self.ranges:
            for match in pattern.finditer(description):
                start, end = match.span()
                if any(start < taken_end and taken_start < end
                       for taken_start, taken_end, _, _ in found):
                    continue
                low, high = match.group(1), match.group(2)
                found.append((start, end, "".join(low.split()), "".join(high.split())))
        found.sort()
        for (_, end, _, _), (start, _, _, _) in pairwise(found):
            between = description[end:start]
            if "/" in between and not any(char.isalpha() for char in between):
                return None
        stated = {(low, high) for _, _, low, high in found}
        if len(stated) != 1:
            return None
        return found[0][2], found[0][3]


@dataclass(frozen=True)
class Profile:
    """The names one manual calls things by, and how much of it must fit."""

    name: str
    # Must capture the command as a group named "command".
    heading: re.Pattern[str]
    syntax_sections: tuple[str, ...]
    parameter_sections: tuple[str, ...]
    # Recognised by its first word alone - see table._table_body for why.
    parameter_header: re.Pattern[str]
    # The two ways a card says it takes none: a word in the name column, and
    # the sentence written out across the row.
    no_parameters_word: re.Pattern[str]
    no_parameters_phrase: re.Pattern[str]
    # Every block a card is built from, needed only by a manual that reached us
    # as a paged conversion: there a block title is a line like any other, and
    # the only way to tell it from content is to have been told the list. A
    # manual that arrives already marked up leaves this empty.
    sections: tuple[str, ...] = ()
    # Which language this manual is written in, as a two-letter code. Two
    # manuals describing the same command both offer a description, and which
    # one an operator wants is the one they can read - see docs.scan_documentation.
    language: str = ""
    # The block that says what the command is for. Read as prose, not as a
    # listing: a manual prints it as bullets or as a paragraph, fenced or not,
    # and either way it is the one sentence the catalog can show an operator.
    purpose_sections: tuple[str, ...] = ()
    # The tail of a header word the name column was too narrow to hold. A
    # header reading "Пара|метр" over two lines leaves "метр" standing as the
    # table's first body line, where it is neither a row nor a description -
    # and, standing above the first row, it was handed to that row's text.
    header_tail: re.Pattern[str] | None = None
    # The running header and footer of such a conversion. The footer is not
    # merely noise to drop: it delimits the page, and the page is what puts the
    # document's two layers back into reading order - see docx.reading_order.
    page_header: re.Pattern[str] | None = None
    page_footer: re.Pattern[str] | None = None
    # How much of a document must look like this before it is read as one.
    min_cards: int = 20
    min_conforming: float = 0.9
    # The words this manual explains its parameters with. A profile without one
    # can still cut a document into cards; it just has no evidence to weigh, so
    # every row of it falls through the ladder to a guess.
    lexicon: Lexicon = field(default_factory=lambda: Lexicon(_NOTHING, _NOTHING, _NOTHING))


# A pattern that matches nothing, for a profile that names no vocabulary. It
# has to be unmatchable rather than empty: an empty pattern matches everywhere.
_NOTHING = re.compile(r"(?!)")


def lexicon_from_dict(data: dict) -> Lexicon:
    """The vocabulary block of a profile. Patterns are case-insensitive.

    An absent key is an unmatchable pattern rather than a permissive one. A
    manual that has not been read yet should fall through to guesses, which the
    measurement counts and reports; a permissive default would instead answer
    every question wrongly and quietly.
    """
    compile_ = lambda pattern: re.compile(pattern, re.IGNORECASE) if pattern else _NOTHING
    return Lexicon(
        domain=compile_(data.get("domain", "")),
        address=compile_(data.get("address", "")),
        effect=compile_(data.get("effect", "")),
        ranges=tuple(re.compile(pattern, re.IGNORECASE) for pattern in data.get("ranges", ())),
    )


def from_dict(data: dict) -> Profile:
    """A profile as written in JSON. Patterns are case-insensitive."""
    compile_ = lambda pattern: re.compile(pattern, re.IGNORECASE)
    optional = lambda key: re.compile(data[key]) if data.get(key) else None
    return Profile(
        name=data["name"],
        heading=re.compile(data["heading"]),
        syntax_sections=tuple(data["syntax_sections"]),
        parameter_sections=tuple(data["parameter_sections"]),
        purpose_sections=tuple(data.get("purpose_sections", ())),
        parameter_header=re.compile(data["parameter_header"]),
        no_parameters_word=compile_(data["no_parameters_word"]),
        no_parameters_phrase=compile_(data["no_parameters_phrase"]),
        language=str(data.get("language", "")),
        sections=tuple(data.get("sections", ())),
        header_tail=optional("header_tail"),
        page_header=optional("page_header"),
        page_footer=optional("page_footer"),
        min_cards=int(data.get("min_cards", 20)),
        min_conforming=float(data.get("min_conforming", 0.9)),
        lexicon=lexicon_from_dict(data.get("lexicon", {})),
    )


def load(path: Path) -> Profile:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@cache
def builtin(name: str) -> Profile:
    """One of the profiles shipped with the package, by file name."""
    return load(PROFILES / f"{name}.json")


def default() -> Profile:
    """The manual this reader was written against."""
    return builtin("l3200_ru")


def available() -> list[Profile]:
    """Every shipped profile, the default one first.

    Order is what makes trying them in turn safe: the manual this reader was
    measured against is offered the document before any profile that has never
    seen one, so a document that both would accept is read the way that was
    proven rather than the way that was guessed.
    """
    names = sorted(path.stem for path in PROFILES.glob("*.json"))
    first = default().name
    ordered = [first] + [name for name in names if name != first]
    return [builtin(name) for name in ordered]
