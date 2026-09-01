"""Keyword or value: the one question the catalog is built on.

A syntax line prints filter rule-number tcp source-ip and says nothing about
which of those words the operator types verbatim and which stand for something
supplied. Compare mode lives or dies on the difference: a keyword must be
matched literally against what the device offers, and a value must be matched
as the placeholder the device's own help prints (<1-2048>).

The evidence is the card's parameter table, read by table. This module only
weighs it, in the order given in mark_parameters.

What counts as evidence is not decided here. A cell states a domain in the
words of the manual it was printed in - "Целое число от 1 до 15" in one,
"with a range of 0~15" in another - and those words are that document's
property, so they live in its profile as a Lexicon. This module holds the
ladder: which question is asked first, and what outranks what. That order is
the same for every manual of this shape, and it is the part that took the
measuring to get right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import Card
from .profile import Lexicon, Profile, default
from .text import TOKEN_RE, stem


def _lookup(name: str, table: dict[str, str]) -> str | None:
    """The table row for a syntax token, trying the spellings a manual uses.

    A parameter is written in the syntax with the suffix that says how it is
    formatted ("src-ip-address/M" for an address with a prefix length) while the
    table lists the bare name, so the suffix is tried both ways round.
    """
    for candidate in (
        name,
        name.split("/")[0],
        name.rstrip(".,;:"),
        # The ordinal belongs to the syntax counting repetitions, not to the
        # name: "next-hop-address2" is the row "next-hop-address" used a second
        # time.
        stem(name),
        stem(name.split("/")[0]),
    ):
        if candidate in table:
            return table[candidate]
    return None


def _depths(line: str) -> list[int]:
    """The brace depth standing at each character of a syntax line."""
    depths: list[int] = []
    depth = 0
    for char in line:
        if char in "{[":
            depth += 1
            depths.append(depth)
        elif char in "}]":
            depths.append(depth)
            depth = max(0, depth - 1)
        else:
            depths.append(depth)
    return depths


def _offers_a_choice(line: str, start: int, end: int) -> bool:
    """Does the group enclosing this token hold more than one thing?

    Asked of the group, not of the token, and only of a separator standing at
    the group's own depth: a pipe inside a nested group belongs to that one.
    """
    depths = _depths(line)
    if start >= len(depths):
        return False
    depth = depths[start]
    if depth == 0:
        return False
    left = start
    while left > 0 and depths[left - 1] >= depth:
        left -= 1
    right = end
    while right < len(line) and depths[right] >= depth:
        right += 1
    return any(
        char == "|" and depths[index] == depth
        for index, char in enumerate(line[left:right], start=left)
    )


def _is_bare_alternative(line: str, start: int, end: int) -> bool:
    """Does this token stand alone as one choice among braced alternatives?

    "management acl { enable | disable }" spells out two keywords the operator
    types verbatim. Their rows in the parameter table describe what choosing
    each one does, which reads like any other row - so the syntax itself has to
    settle it, and a token with nothing but a separator on either side is a
    literal choice rather than a value to be supplied.

    A group holding one thing is not that. "[ port-id ]" says the operator may
    supply a port number or leave it out - the brackets make it optional, and
    optional is not a choice between spellings. Reading it as one marked the
    same row a keyword here and a value where the manual printed it bare, so
    one card answered the same question two ways - defect 3 in
    docs/DOCPARSE_DEFECTS_RU.md.
    """
    before = line[:start].rstrip()
    after = line[end:].lstrip()
    if bool(before) and before[-1] in "{[|" and bool(after) and after[0] in "}]|":
        touches = before[-1] == "|" or after[0] == "|"
        return touches or _offers_a_choice(line, start, end)
    # An alternative this manual writes without braces: "cipher | plain". The
    # pipe alone says these are two spellings of one choice, and a choice is
    # typed verbatim.
    touches_pipe = (bool(before) and before[-1] == "|") or (bool(after) and after[0] == "|")
    return touches_pipe


def placeholder(name: str, description: str, lexicon: Lexicon | None = None) -> str:
    """How a parameter is written once it is known to be one.

    A single unambiguous numeric domain becomes the range the device's own help
    prints, so the two sides of a compare are talking about the same token. Two
    ranges in one cell mean the domain is per-model, and inventing one of them
    would be a claim the manual does not make - the name is used instead, which
    is true for every model.
    """
    lexicon = lexicon if lexicon is not None else default().lexicon
    if lexicon.address.search(description):
        # An address is not a number, whatever numbers stand near it. Rows for
        # "ipv4-address" carry ranges that drifted in from the row above, and a
        # placeholder of "<0-255>" for an IP address is worse than no domain at
        # all: it claims the device asks for one octet.
        return f"<{name}>"
    found = lexicon.range_in(description)
    if found is not None:
        return f"<{found[0]}-{found[1]}>"
    return f"<{name}>"


def _mark_compound(token: str, table: dict[str, str], lexicon: Lexicon) -> str:
    """Mark the parts of a dotted compound the table knows individually.

    Returned unchanged unless at least one part resolves, so an ordinary token
    that merely contains a dot is never taken apart.
    """
    if "." not in token:
        return token
    parts = token.split(".")
    described = [_lookup(part, table) for part in parts]
    if not any(description is not None for description in described):
        return token
    return ".".join(
        placeholder(part, description, lexicon) if description is not None else part
        for part, description in zip(parts, described)
    )


KEYWORD = "keyword"
VALUE = "value"


@dataclass(frozen=True)
class Mark:
    """What one token of a syntax line turned out to be, and on what evidence.

    The evidence is kept because it is the only way to audit a catalog of four
    thousand rows without reading the manual again: a run of parameters all
    settled by ``default`` means the table described them in a way no rule here
    recognises, and that is a finding about the reader, not about the device.
    """

    token: str
    kind: str
    text: str
    reason: str
    description: str = ""


def _command_prefix(command: str, line: str, matches: list) -> int:
    """How many of the line's leading tokens spell the card's own command name.

    The heading is the one thing about a card that is not a table and cannot be
    damaged by a column edge, so it settles the question no other rule can:
    "enable password level level-value cipher | plain password" opens with the
    name of the command being documented, and that opening "password" is typed
    verbatim however the table describes the word. Without this the reader
    marked the command's own name as a value and every such command was
    reported missing from the device.
    """
    wanted = TOKEN_RE.findall(command)
    printed = [m.group(0) for m in matches]
    # A card documents the negative form of its command alongside the positive
    # one, so the line may open with a "no" the heading does not carry. Skipped
    # only when the heading does not carry it: a card headed "no mac-address"
    # matches that "no" itself, and skipping it slid the comparison one token
    # along, failed on the first word and left the command's own name to be
    # marked a value - "no mac-address" was read as "no <mac-address>".
    start = 1 if printed[:1] == ["no"] and wanted[:1] != ["no"] else 0
    matched = 0
    while matched < len(wanted) and start + matched < len(printed):
        if printed[start + matched] != wanted[matched]:
            break
        matched += 1
    return start + matched if matched else 0


def _without_own_name(token: str, description: str) -> str:
    """The row's text with the leading repeat of its own name taken off.

    The name column bleeds into the description on nearly every card of this
    manual - the row for "drop" reads "drop Отбрасывание пакета" - and both
    tests below are anchored at the start of the text. Anchoring them behind
    the row's own name is what lets a description be recognised at all.
    """
    stripped = re.sub(
        rf"^[\s({{]*{re.escape(token)}[\s)}}:.,—–-]*", "", description, count=1, flags=re.IGNORECASE
    )
    return stripped.strip()


def _decide(
    token: str,
    description: str | None,
    line: str,
    start: int,
    end: int,
    at_depth: int,
    repeated: set[str],
    table: dict[str, str],
    lexicon: Lexicon,
) -> Mark:
    """Weigh one token against the card's table. See ``mark_parameters``."""
    if description is not None:
        description = _without_own_name(token, description)
        if not description:
            # The table named the row and said nothing else - "(preference)",
            # "(single-connection)". A row that describes nothing is evidence
            # of nothing, and the token is left as the manual prints it.
            return Mark(token, KEYWORD, token, "named-only")
    if description is None:
        # A subinterface is written as two parameters joined by a dot
        # ("interface-number.subinterface-number"); the table lists them
        # separately, so the compound is marked part by part.
        marked = _mark_compound(token, table, lexicon)
        if marked != token:
            return Mark(token, VALUE, marked, "compound")
        return Mark(token, KEYWORD, token, "untabled")
    if token in repeated and at_depth == 0:
        return Mark(token, KEYWORD, token, "named-then-valued", description)
    if _is_bare_alternative(line, start, end):
        # Weighed before the domain, because the syntax line is evidence and
        # the description is wreckage: the row for "cipher" in
        # "cipher | plain" ends "отображаемый в командной строке", and the word
        # "строке" reads as a string domain to any rule looking for one.
        return Mark(token, KEYWORD, token, "alternative", description)
    if lexicon.domain.search(description):
        return Mark(token, VALUE, placeholder(token, description, lexicon), "domain", description)
    if lexicon.effect.match(description):
        return Mark(token, KEYWORD, token, "effect", description)
    # Left over: a row that named the token but described it in neither way.
    # Treated as a parameter, because the cost is asymmetric - a placeholder too
    # many makes a compare match loosely, a keyword too many reports a command
    # the device really has as missing.
    return Mark(token, VALUE, placeholder(token, description, lexicon), "default", description)


def mark_card(card: Card, profile: Profile | None = None) -> tuple[list[str], list[Mark]]:
    """The card's syntax lines with parameters marked, and every decision made.

    Keywords are left exactly as the manual prints them; only tokens the card's
    own parameter table accounts for are rewritten. The tests run in the order
    of their evidence:

    1. the table never mentions the token - it is a keyword, and this settles
       roughly three quarters of every line;
    2. the token appears both outside the braces and inside them, which is how
       this manual writes "name the setting, then give its value"
       ("radius-server deadtime { deadtime | default }") - the outer one is the
       keyword being named, the inner one is the value;
    3. the table states a domain of values - a parameter, written as the range
       the device's own help prints when the domain gives one;
    4. the token stands alone as a braced alternative - a literal choice;
    5. the row describes an effect rather than a value - a literal choice.

    A token appearing on several lines of one card is decided once per line,
    because the tests are about where it stands: the same word is a keyword
    outside the braces and a value inside them.
    """
    lexicon = (profile or default()).lexicon
    marked: list[str] = []
    marks: list[Mark] = []
    for line in card.syntax:
        depths = _depths(line)
        matches = list(TOKEN_RE.finditer(line))
        outer = {m.group(0) for m in matches if depths[m.start()] == 0}
        inner = {m.group(0) for m in matches if depths[m.start()] > 0}
        repeated = outer & inner

        keyword_head = _command_prefix(card.command, line, matches)
        pieces: list[str] = []
        cursor = 0
        for position, match in enumerate(matches):
            token = match.group(0)
            pieces.append(line[cursor : match.start()])
            cursor = match.end()
            if position < keyword_head:
                mark = Mark(token, KEYWORD, token, "command-name")
                marks.append(mark)
                pieces.append(token)
                continue
            following = matches[position + 1].group(0) if position + 1 < len(matches) else None
            if following == token:
                # "igmp-snooping router-aging-time router-aging-time": the
                # manual names the setting and then asks for its value. Only an
                # immediate repetition counts - "HH:MM:SS DD MM YYYY" repeats
                # MM at a distance and both are values.
                mark = Mark(token, KEYWORD, token, "named-then-valued")
                marks.append(mark)
                pieces.append(token)
                continue
            mark = _decide(
                token,
                _lookup(token, card.parameters),
                line,
                match.start(),
                match.end(),
                depths[match.start()],
                repeated,
                card.parameters,
                lexicon,
            )
            marks.append(mark)
            pieces.append(mark.text)
        pieces.append(line[cursor:])
        marked.append("".join(pieces).strip())
    return marked, marks


def mark_parameters(card: Card, profile: Profile | None = None) -> list[str]:
    """The card's syntax lines alone, for callers with no use for the evidence."""
    return mark_card(card, profile)[0]
