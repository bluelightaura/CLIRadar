"""A manual that arrived as a .docx made from a PDF, put back into reading order.

The reference this package was written against came as a structured Markdown
file. The second one came as ``.docx``, and the natural assumption - a Word
document has tables, so the table survived - is false. The file holds **no**
``w:tbl`` at all: 127 thousand paragraphs and not one table. It is a PDF
converted to Word, so every rendered *line* became a paragraph, and a table row
became a paragraph with its columns run together by single spaces:

    level-value specifies the integer form of the command level, with a range of 0~15.

That turns out to be good news rather than bad. The row is delimited by the
paragraph, which is exactly what the first manual had lost, and the name stands
first in it - so the reader downstream needs no new way of finding rows. What
it needs is the text in the order a person reads it, and that is what this
module produces: the same convention ``cards.py`` already parses - a heading, a
``**Block**`` title, fenced content - so that nothing below has to learn what a
.docx is.

Two things have to be undone first, and both were established against the PDF
rather than guessed:

**The document has two layers, and they do not duplicate each other.** Every
paragraph carries ``w:ind``; in one layer that element has no attributes, and
in the other it carries ``left``/``right`` offsets in twips. Pages alternate
between the two - 1085 command headings live in the first layer, 838 in the
second, and only two appear in both - so reading either alone loses half the
manual.

**The positioned layer runs one page behind.** Cutting the stream at the
running footer, the text of page N is the positioned paragraphs of interval
N+1 followed by the flow paragraphs of interval N. This is not a guess: rebuilt
that way, 27 of 35 sampled pages match ``pdftotext -layout`` word for word,
while the obvious rival - both layers of the same interval - matches none. The
eight that differ are all rows that wrap inside a parameter table, where the
version recovered here is the more faithful of the two: pdftotext interleaves
the columns mid-sentence, and this does not.

``pdftotext`` was the oracle for that check and is not a dependency: what this
costs the project is ``zipfile`` and ``xml.etree``, both of the standard
library.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 - operator's own manual, not network input
import zipfile
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path

from .profile import Profile, default

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A line of a table of contents, which repeats every heading in the manual and
# would otherwise open a card per entry. The leader dots are unmistakable.
LEADER_RE = re.compile(r"\.{4,}")

# How many consecutive paragraphs may be joined looking for a block title. The
# conversion breaks a title across lines when the column is narrow -
# "Parameter" then "Description", "Command" then "Guidance" - and two pieces is
# as far as this manual ever splits one.
TITLE_REACH = 3


def _leaf_paragraphs(stream) -> Iterator[tuple[bool, str]]:
    """Every paragraph holding text of its own, as (positioned, text).

    Paragraphs nest: the one anchoring a text frame contains the frame's own
    paragraphs, and asking it for its text returns the whole page run together.
    Only the leaves are read, which drops that duplicate without a rule about
    what it looks like.

    The tree is discarded as it is walked - the XML runs to 113 MB, and holding
    it would cost more memory than the rest of the program uses.
    """
    context = ET.iterparse(  # nosec B314 - operator's own manual, not network input
        stream, events=("start", "end")
    )
    body = None
    depth = 0
    for event, element in context:
        if event == "start":
            if body is None and element.tag == W + "body":
                body = element
            if element.tag == W + "p":
                depth += 1
            continue
        if element.tag != W + "p":
            continue
        depth -= 1
        if element.find(".//" + W + "p") is None:
            properties = element.find(W + "pPr")
            indent = properties.find(W + "ind") if properties is not None else None
            # The discriminator is the attribute, not the element: every
            # paragraph has a w:ind, and only the positioned layer's carries a
            # left offset.
            positioned = indent is not None and indent.get(W + "left") is not None
            text = "".join(run.text or "" for run in element.iter(W + "t")).strip()
            if text:
                yield positioned, text
        if depth == 0 and body is not None:
            body.clear()


def _positioned_pages(positioned: list[str]) -> list[list[str]]:
    """One interval's positioned half, cut into the pages it holds.

    Usually it holds one, and its first line is that page's running header.
    Sometimes it holds two, and then the same header stands again in the
    middle of it - which is what makes the cut possible without a list of
    header wordings: the page announces its own header on the first line, and
    every repetition of that line starts another page.

    Getting this wrong is not a cosmetic matter. An interval holding two pages
    shifts every following page by one, and a shifted page hands one card's
    syntax to the card below it - "tftp get" printed its four forms inside the
    card for "tftp put", where nothing downstream could tell they were wrong.
    """
    if not positioned:
        return []
    header = positioned[0]
    pages: list[list[str]] = [[]]
    for line in positioned[1:]:
        if line == header:
            pages.append([])
            continue
        pages[-1].append(line)
    return pages


def _intervals(
    paragraphs: Iterable[tuple[bool, str]], footer: re.Pattern[str] | None
) -> Iterator[tuple[list[str], list[list[str]]]]:
    """The stream cut at the running footer: one interval's flow, and its pages.

    The first positioned line of an interval is the page's running header and
    does not survive this. That is a structural fact rather than a guess about
    its wording, and it was checked before being relied on: of 1562 intervals
    with a positioned half, 1560 begin with one. Two do not - the cover, and
    one line of sample output - and losing those two costs less than what
    listing header wordings costs, which is every header the list does not
    happen to name. This manual alone runs them as "Chapter 5 Routing
    Commands", as a bare "Management Commands", and on some pages in Chinese;
    left in, each welds itself onto whichever form was open when the page
    turned.
    """
    flow: list[str] = []
    positioned: list[str] = []
    for is_positioned, text in paragraphs:
        if footer is not None and footer.match(text):
            yield flow, _positioned_pages(positioned)
            flow, positioned = [], []
            continue
        (positioned if is_positioned else flow).append(text)
    if flow or positioned:
        yield flow, _positioned_pages(positioned)


def reading_order(
    intervals: Iterable[tuple[list[str], list[list[str]]]]
) -> Iterator[str]:
    """The manual's lines in the order they are printed.

    The positioned half of the document runs a page behind its flow half, so
    an interval's flow is held back until a positioned page has been seen to
    put in front of it. The pages wait in a queue rather than being taken from
    the interval directly, because an interval does not always carry exactly
    one: carrying two would otherwise shift every page after it.
    """
    waiting: deque[list[str]] = deque()
    held: list[str] = []
    for flow, pages in intervals:
        waiting.extend(pages)
        if waiting:
            yield from waiting.popleft()
        yield from held
        held = flow
    yield from held
    while waiting:
        yield from waiting.popleft()


def strip_furniture(source: Iterable[str], profile: Profile | None = None) -> list[str]:
    """The lines that are the manual, without the ones that are the page.

    Two kinds go. The running header repeats on every page and would land
    inside whichever block was open when the page turned. The table of contents
    is worse than noise: it names every command in the manual, so left in it
    opens a card per entry and fills each with the next entry's title.
    """
    header = (profile or default()).page_header
    return [
        line
        for line in source
        if not LEADER_RE.search(line) and not (header is not None and header.match(line))
    ]


def lines(path: Path | str, profile: Profile | None = None) -> list[str]:
    """Every line of the document, page furniture and contents removed."""
    profile = profile or default()
    with zipfile.ZipFile(path) as archive, archive.open("word/document.xml") as stream:
        ordered = reading_order(_intervals(_leaf_paragraphs(stream), profile.page_footer))
        return strip_furniture(ordered, profile)


def _title_at(source: list[str], index: int, titles: dict[str, str]) -> tuple[str, int]:
    """The block title starting here and how many lines it took, or ("", 0).

    Matching is whole and shortest-first, which is what keeps the parameter
    table's own header row out of it: this manual titles the block *Parameter
    Description* and then heads the table *Parameter Description Values*, and
    only the first is a title.

    Case is not part of the name. The same manual heads one card *Command
    Format* and the next *Command format*, and writes *Parameter description
    values* where it elsewhere writes *Parameter Description Values*; a
    case-sensitive match reads those cards as having no syntax block at all.
    """
    for span in range(1, TITLE_REACH + 1):
        if index + span > len(source):
            break
        joined = " ".join(source[index : index + span]).casefold()
        if joined in titles:
            return titles[joined], span
    return "", 0


# The manual's notation for "this command, optionally negated", written on the
# form itself rather than as a second form. The space after it is not reliable:
# one card prints "[no]join stack-port 1" closed up.
OPTIONAL_NO_RE = re.compile(r"^\[\s*no\s*\]\s*")


def _named_by(first: str, openers: set[str]) -> bool:
    """Is this the first word of a form, given what the card's heading offers?

    The heading's own words are matched by prefix, because it offers a stem as
    often as a word - "ipv4[ipv6]-family" heads "ipv4-family" and "ipv6-family".
    The negation word is matched whole: a form begins with ``no`` and a space,
    while prefix-matching it also swallows every note the manual opens with
    "Note:", "Nothing" or "Non-specified".
    """
    return any(
        first == opener if opener == "no" else first.startswith(opener)
        for opener in openers
    )


def _opens_a_form(text: str, openers: set[str]) -> str | None:
    """The form this line starts, spelled as the operator would type it.

    The card's heading says what its forms are called, so a form begins with
    that name or with ``no``. The test is on the prefix rather than on equality
    because a heading offers a stem as often as a word: "ipv4[ipv6]-family"
    heads forms called "ipv4-family" and "ipv6-family".

    Two ways the manual writes a form the plain prefix test does not recognise,
    and both cost the card its whole syntax block:

    - it marks an optional negation on the form - "[no] iss master" - instead
      of printing the negated form separately;
    - it capitalises the first letter of the line - "Interface stack-port 1"
      under a card headed "interface stack-port".

    Only the first letter is forgiven, never the rest: the same block carries
    prose notes and leaked table cells, and folding case throughout would let
    a sentence opening with the command's own name become a form of it. The
    heading is authoritative for the spelling, so the letter is put back down.
    """
    words = text.split()
    if not words:
        return None
    negated = OPTIONAL_NO_RE.match(text)
    if negated:
        text = f"[no] {text[negated.end():]}"
        words = text.split()[1:]
        if not words:
            return None
    first = words[0]
    if _named_by(first, openers):
        return text
    # Forgiving the capital is only safe on a line that is otherwise a command.
    # The block also carries prose the manual set in title case ("Show
    # IGMP-Snooping Statistic Interface Eth-Trunk Trunk-Number"), and lowering
    # its first letter alone turns a sentence into a form of the command it
    # describes. A real form in this manual is lower case throughout.
    lowered = text[:1].lower() + text[1:]
    if (
        lowered != text
        and not any(c.isupper() for c in lowered)
        and _named_by(lowered.split()[0], openers)
    ):
        return lowered
    return None


# What an unfinished line looks like from either end. A form that outran its
# printed line breaks at a place the notation shows: after the hyphen inside a
# name, after the bar between alternatives, inside the braces around them - or
# the break falls just before one of those marks instead of after it.
CONTINUES_AFTER = ("-", "|", "{", "[", ",", "/")
CONTINUES_BEFORE = ("|", "}", "]", ",")


def _forms(block: list[str], command: str) -> list[str]:
    """The command forms a syntax block states, with printed line breaks undone.

    Two things are wrong with the block as it arrives, and one rule settles
    both. One form runs to eight printed lines in this manual, and left as
    printed seven of them enter the catalog as commands of their own. And the
    block is not only syntax: the manual prints notes inside it ("This command
    can be used to send messages..."), the conversion drops a table cell into
    it, and on some pages a Chinese header lands in the middle of it.

    An earlier version joined every line that did not open a form, which fixed
    the wrapping and made the prose worse - it welded the notes onto the
    command. The rule is the other way round: a line is joined only where the
    line above it is visibly unfinished, or where it visibly continues one. A
    line that is neither that nor the start of a form is not a command form at
    all, and is dropped rather than glued to one.

    A line broken mid-name keeps the hyphen it broke on, so it is closed up
    without a space: "bgp-af-ipv4-" and "mcast" are "bgp-af-ipv4-mcast", not
    "bgp-af-ipv4mcast", which no device has ever offered.
    """
    openers = {"no"}
    if command.split():
        # A heading is not always one word - this manual heads a card
        # "ping|ping6" - and taken whole it would leave "ping mac-address"
        # looking like a continuation, welding two forms into one.
        openers.update(re.findall(r"[A-Za-z][A-Za-z0-9-]*", command.split()[0]))

    forms: list[str] = []
    for line in block:
        text = line.strip()
        if not text:
            continue
        previous = forms[-1].rstrip() if forms else ""
        if previous and (
            previous.endswith(CONTINUES_AFTER) or text.startswith(CONTINUES_BEFORE)
        ):
            forms[-1] = previous + text if previous.endswith("-") else f"{previous} {text}"
            continue
        opened = _opens_a_form(text, openers)
        if opened:
            forms.append(opened)
    return forms


def as_card_text(source: list[str], profile: Profile | None = None) -> str:
    """The document rewritten in the convention ``cards.split_cards`` reads.

    Nothing is interpreted here beyond where a card starts and where each of
    its blocks starts. What a block means is decided downstream, by a reader
    that has no idea this document was ever a PDF.
    """
    profile = profile or default()
    titles = {title.casefold(): title for title in profile.sections}
    out: list[str] = []
    block: list[str] = []
    section = ""
    command = ""

    def flush() -> None:
        nonlocal block
        if not section:
            block = []
            return
        body = _forms(block, command) if section in profile.syntax_sections else block
        out.append(f"**{section}**")
        out.append("```")
        out.extend(body)
        out.append("```")
        block = []

    index = 0
    while index < len(source):
        line = source[index]
        heading = profile.heading.match(line)
        if heading:
            flush()
            section = ""
            command = heading.group("command").strip()
            out.append(line)
            index += 1
            continue
        title, span = _title_at(source, index, titles)
        if title:
            flush()
            section = title
            index += span
            continue
        block.append(line)
        index += 1
    flush()
    return "\n".join(out)


def read(path: Path | str, profile: Profile | None = None) -> str:
    """One .docx manual, as text this package's card reader understands."""
    profile = profile or default()
    return as_card_text(lines(path, profile), profile)
