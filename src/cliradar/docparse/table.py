"""The parameter table: what it says, once the conversion is undone.

This is the half of a card worth having. A manual of this shape writes
parameters as bare words - filter rule-number tcp ... - so nothing in the
syntax line marks rule-number as a value to be supplied rather than a
keyword to be typed. The table says which is which, and often says the domain
too ("Целое число от 1 до 2048"), which is exactly the placeholder the device's
own help emits. Without this the compare mode is worthless against such a
manual: every documented command would be reported missing, because a keyword
spelling can never equal a placeholder.

What makes the table hard is that it is a rendered table that survived a
conversion to plain text: the gutters are gone, long names overflow their
column, descriptions wrap into the wrong one, and words break across a column
edge mid-syllable. So the rows are not found by geometry. They are found
against the card's own syntax, which names every parameter the table can
legitimately mention - see _syntax_tokens.

The table is read per card and never pooled. Cards list enumerated values in the
same column as parameter names - enable and disable are rows in the
table of management acl { enable | disable } - so a document-wide vocabulary
would turn those keywords into placeholders everywhere they appear. Scope is
what keeps the rule honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .profile import Profile, default
from .text import TOKEN_RE, stem

# Where the header pattern lives and why only its first word is trusted: see
# profile.py.
# How far right of the header's own indent a name may still start. The name
# column is left-aligned, but not flush: a card indents "pwd" four spaces under
# a header that begins at column zero. Wider than a name column ever is, and
# still far left of where a wrapped description sits.
NAME_COLUMN_SLACK = 8


@dataclass(frozen=True)
class _Vocabulary:
    """The words a card's own syntax vouches for.

    Exact spellings and stems are kept apart on purpose. A row is opened only
    on an exact token, because a stem is a shorter word and a shorter word
    matches more of the surrounding Russian prose than it should. Stems earn
    their keep only where a name is already known to be damaged, where the
    alternative is not reading the row at all.
    """

    tokens: frozenset[str]
    stems: frozenset[str]
    # Every known word by its lowercase spelling, mapped to the spelling the
    # syntax used. A table capitalises the first word of a row - "Isis-instance
    # is the integer form of the ISIS instance number" - where the syntax
    # prints "isis-instance", and a case-sensitive lookup loses the whole row
    # over it. Case is the conversion's business, not the device's; what goes
    # into the catalog is always the syntax's spelling, because that is what
    # the operator types.
    folded: dict[str, str] = field(default_factory=dict)

    def knows(self, candidate: str) -> bool:
        return self.spell(candidate) is not None

    def spell(self, candidate: str, folding: bool = False) -> str | None:
        """The syntax's spelling of a word, allowing for how a row is capitalised.

        By default the only difference forgiven is a capital on the first
        letter, which is what a manual puts there because a row opens a
        sentence: "Isis-instance is the integer form of the ISIS instance
        number". Forgiving more is not a smaller favour than it looks. A
        description too long for its column wraps into the name column, and
        what stands at the head of such a line is very often the feature's own
        name in the prose's capitalisation - MAC-VLAN, ARP-Proxy, StateMachine
        - which a full fold hands a row of its own, and that row then takes the
        text belonging to the real parameter below it. Measured on the Russian
        manual, full folding invented nineteen such rows and cost four real
        ones.

        ``folding`` is for a candidate spelled by more than one word of the
        name column. That candidate had to be assembled before it could be
        looked up, and the syntax knowing the assembly is evidence no single
        capitalised word carries.
        """
        if candidate in self.tokens or candidate in self.stems:
            return candidate
        known = self.folded.get(candidate.lower())
        if known is None:
            return None
        if folding or candidate[1:] == known[1:]:
            return known
        return None

    def completions(self, prefix: str) -> list[str]:
        """Every name the syntax knows that this fragment could be the start of.

        Shortest first: a fragment of "next-hop-address1" is a fragment of the
        name "next-hop-address" before it is a fragment of any one of its four
        numbered occurrences, and the name is what the table row is about.
        """
        return sorted(
            {
                word
                for word in self.tokens | self.stems
                if word.startswith(prefix) and word != prefix
            },
            key=lambda word: (len(word), word),
        )


def _vocabulary(syntax: list[str]) -> _Vocabulary:
    tokens = _syntax_tokens(syntax)
    stems = {stem(token) for token in tokens if stem(token) != token}
    folded: dict[str, str] = {}
    # Longest first, so that where two spellings fold together the fuller name
    # is the one a row is opened on. Length alone does not order them: a card
    # can print two spellings of the same length that fold together - "show
    # mac-address MAC-ADDRESS" carries both - and with only length to go on the
    # winner came out of set iteration, which is to say out of the process's
    # hash seed. That is worse than it sounds: the catalog differed between
    # runs of the same reader on the same file, so diffing two catalogs, which
    # is how every change here is checked, could not tell a real change from
    # the seed. Ties go to the spelling that sorts first, and on the one card
    # where it decides anything that is the right reading - taking the other
    # splits the row for "security" in two and gives half of it a name the
    # table never described.
    for word in sorted(tokens | stems, key=lambda w: (-len(w), w)):
        folded.setdefault(word.lower(), word)
    return _Vocabulary(tokens=frozenset(tokens), stems=frozenset(stems), folded=folded)


def _syntax_tokens(syntax: list[str]) -> set[str]:
    """Every token the card's own syntax lines spell out.

    This is the reader's anchor. A parameter table is a rendered table that
    survived a conversion to plain text, and nothing about its whitespace can
    be relied on - but a row that names a parameter names one that the syntax
    above it also names. Anything else the table appears to say is wreckage:
    a description that wrapped into the name column, a title welded to a value,
    a word split across a column edge. Checking each candidate against this set
    discards all of it without a single rule about how the damage looks.
    """
    tokens: set[str] = set()
    for line in syntax:
        for match in TOKEN_RE.finditer(line):
            token = match.group(0)
            tokens.add(token)
            tokens.add(token.split("/")[0])
            tokens.update(token.split("."))
    return tokens


def parameter_rows(
    lines: list[str], syntax: list[str] | None = None, profile: Profile | None = None
) -> dict[str, str]:
    """One parameter table, as name to the text describing it.

    The table has three columns - a name, prose about it, and the values it
    admits - and the third is the one worth having: it carries the domain
    ("Целочисленное значение в диапазоне 30-86400") that says a token is a
    value to be supplied rather than a keyword to be typed. It is also the
    column the conversion damaged most, because a long description bleeds
    across the column edge and welds itself into the values mid-word. So the
    two right-hand columns are read together as one body of text rather than
    separated: every question this reader goes on to ask - is a domain stated,
    is an effect described - is answered the same way whichever column the
    words landed in, and joining them costs nothing while splitting them
    wrongly costs the row.

    What has to be exact is the name, and it is not found by geometry. An
    earlier reader split each line at its first run of two spaces, which fails
    the moment a name fills its column: "privilege-value Уровень разрешений" is
    separated by one space, and the whole line becomes a parameter no device
    has. Names are found against the card's own syntax instead - see
    ``_row_starts`` - and the table is then sliced at the lines where they were
    found, each row taking the text down to the next one.

    Description text standing above the first row is not dropped. The values
    column sometimes sets its first line before the name it belongs to, on
    about one card in twenty-five, and that text is handed to the first row
    rather than to nothing.
    """
    vocabulary = _vocabulary(syntax or [])
    body, indent, edge = _table_body(lines, profile or default())
    if body is None:
        return {}

    starts = _row_starts(body, indent, edge, vocabulary)
    if not starts:
        # Nothing the syntax vouches for. Either the table says nothing, or the
        # card has no usable syntax to vouch with - a manual writes "(См.
        # перечень команд выше.)" where the forms should be, and enumerates a
        # parameter's values where its name should be. Only for those cards is
        # geometry consulted, and only because the alternative is losing the
        # table outright.
        starts = _row_starts_by_shape(body, indent, edge)
    if not starts:
        return {}

    rows: dict[str, str] = {}
    bounds = [index for index, _ in starts] + [len(body)]
    preamble = " ".join(body[: bounds[0]]).split()
    for position, (index, name) in enumerate(starts):
        text = " ".join(body[index : bounds[position + 1]]).split()
        if position == 0:
            text = preamble + text
        rows[name] = " ".join(text)
    return rows


PLAIN_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MIN_SHAPE_NAME = 3


def _row_starts_by_shape(
    body: list[str], indent: int, edge: int | None
) -> list[tuple[int, str]]:
    """Rows found by how they sit, for a card whose syntax cannot vouch for them.

    The last resort, and deliberately strict about what it will accept: a
    single Latin word in the name column, spelled the way a parameter is
    spelled, with text to its right. It is the rule this reader was built to
    replace - geometry is exactly what the conversion destroyed - so it runs
    only where syntax anchoring found nothing at all, and where a wrong row
    costs a card that was going to be lost anyway.
    """
    starts: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(body):
        stripped = line.strip()
        if not stripped:
            continue
        column = len(line) - len(line.lstrip())
        if column > indent + NAME_COLUMN_SLACK:
            continue
        head, _, rest = stripped.partition(" ")
        if not rest.strip():
            continue  # a name with nothing said about it is not a row
        if edge is not None and column + len(head) > edge:
            continue
        if len(head) < MIN_SHAPE_NAME or not PLAIN_NAME_RE.match(head):
            continue
        if head in seen:
            continue
        starts.append((index, head))
        seen.add(head)
    return starts


def _table_body(
    lines: list[str], profile: Profile | None = None
) -> tuple[list[str] | None, int, int | None]:
    """The table's rows, with the two positions its header fixes.

    Returns the lines below the header with the name fragments still in place,
    the indent the header starts at, and where its second column begins if that
    can still be seen. A block with no header yields nothing, and that is the
    intended failure: every token of the card then reads as a keyword, which is
    how the line reader behaved before this module existed.
    """
    header_re = (profile or default()).parameter_header
    for position, line in enumerate(lines):
        header = header_re.match(line)
        if header is None:
            continue
        indent = len(header.group("indent"))
        second = header.group("second")
        edge = line.index(second, indent) if second else None
        body = [rest for rest in lines[position + 1 :] if rest.strip()]
        return body, indent, edge
    return None, 0, None


def _row_starts(
    body: list[str], indent: int, edge: int | None, vocabulary: _Vocabulary
) -> list[tuple[int, str]]:
    """Where each row begins and what it is called.

    Names are looked for in three places, because the conversion put them in
    four. Most sit where a table puts them, at the head of the line in the
    name column. Some were welded into the neighbouring prose when a
    description outgrew its column - "Строковый    форsrcfile", "Путь к
    файdirectory" - and no care about whitespace will find them there; what
    finds them is that the prose is Russian and a name is not, so a name is a
    Latin island the syntax vouches for. And some were broken in half by a name
    column too narrow to hold them, one piece per line, sometimes keeping the
    hyphen it broke on ("vty-" then "number") and sometimes eating it
    ("skip" then "lines1", for "skip-lines1"). The worst of that third kind
    lost its tail inside a neighbouring word - "interface-" in the name column
    and "number" welded to the end of "Switchnumber" three columns to the
    right - and is looked for by asking the syntax what a fragment could have
    been rather than by looking for a word that is no longer there. And in the
    fourth the gutter itself is gone, so the name and the description it
    introduces arrive as one word - "sendIGMP Protocol Send Message Debug
    Information" - which is the same damage seen from the other end, and
    ``_welded_head`` reads it that way.

    The syntax is what makes all three safe. A fragment means nothing on its
    own, but the card states its own vocabulary directly above, so a candidate
    is accepted only when that vocabulary contains it - or, for a broken name,
    contains what two fragments spell between them. A row already claimed is
    never claimed twice, which is what stops a description that mentions an
    earlier parameter from opening a second row for it.
    """
    starts: list[tuple[int, str]] = []
    seen: set[str] = set()
    consumed: set[int] = set()

    for index, line in enumerate(body):
        if index in consumed:
            continue
        found = _whole_name(line, indent, edge, vocabulary, seen)
        if found is None:
            found = _broken_name(body, index, vocabulary, seen, consumed)
        if found is None:
            found = _welded_name(body, index, indent, edge, vocabulary, seen)
        if found is None:
            found = _welded_head(line, indent, edge, vocabulary, seen)
        if found is None:
            continue
        starts.append((index, found))
        seen.add(found)
    return starts


def _whole_name(
    line: str, indent: int, edge: int | None, vocabulary: _Vocabulary, seen: set[str]
) -> str | None:
    """A name that survived the conversion in one piece, on this line.

    In the name column the row is opened on the longest name the syntax
    vouches for, not the first: a name column too narrow for "process-id"
    prints it as two words, "Process ID", and the first of them is a keyword
    the same card also uses. Opening the row on "process" would name it after
    a different token of the same command and attach this row's domain to it.

    Only there is case forgiven, and only the capital that opens a row.
    Anywhere else on the line there is no vouching from position at all, so the
    spelling has to be exact - see ``_Vocabulary.spell``.
    """
    stripped = line.lstrip()
    position = len(line) - len(stripped)
    in_name_column = position <= indent + NAME_COLUMN_SLACK and (
        edge is None or position < edge
    )
    if in_name_column:
        found = _longest_at_head(stripped, vocabulary, seen)
        if found is not None:
            return found
    # Off the name column the spelling has to be exact. A row found anywhere on
    # the line is found by the word alone, with nothing about where it stands
    # to vouch for it, and folding case there lets an ordinary capitalised word
    # of the description open a row - 260 of them on the Russian manual.
    for match in TOKEN_RE.finditer(line):
        token = match.group(0)
        if token in vocabulary.tokens and token not in seen:
            return token
    return None


# How many words of the name column may be joined back into one name. A name
# column narrow enough to split "process-id" splits it in two; nothing in
# either manual is broken into three.
HEAD_WORDS = 2


def _longest_at_head(stripped: str, vocabulary: _Vocabulary, seen: set[str]) -> str | None:
    """The longest name the syntax knows, spelled by the row's opening words.

    Both joinings are tried for the same reason ``_broken_name`` tries both:
    the column edge either kept the hyphen the name was split on or ate it.

    The second word has to stand right against the first. A name column splits
    a name into two words with one space between them; a gap wider than that is
    the gutter, and what lies across it belongs to another column. Without the
    test, the row "groupnum   Идентификатор группы   Целое число от 1 до 4"
    joins its name to the "1" of its own domain and opens as "groupnum1" - a
    name this card's syntax does use, on another form, which is what makes the
    mistake survive every check downstream.
    """
    found = list(TOKEN_RE.finditer(stripped))[:HEAD_WORDS]
    if not found:
        return None
    words = [match.group(0) for match in found]
    candidates = [words[0]]
    if len(words) > 1 and stripped[found[0].end() : found[1].start()] == " ":
        candidates += [f"{words[0]}-{words[1]}", f"{words[0]}{words[1]}"]
    best: str | None = None
    for position, candidate in enumerate(candidates):
        spelling = vocabulary.spell(candidate, folding=position > 0)
        if spelling is None or spelling in seen or spelling not in vocabulary.tokens:
            continue
        if best is None or len(spelling) > len(best):
            best = spelling
    return best


# How far below a fragment its other half may be. The two pieces of a broken
# name are usually adjacent, but a description wrapping between them pushes
# them apart, and two lines is as far as this manual ever throws them.
BROKEN_NAME_REACH = 2


def _broken_name(
    body: list[str],
    index: int,
    vocabulary: _Vocabulary,
    seen: set[str],
    consumed: set[int],
) -> str | None:
    """A name the name column split across lines, rejoined against the syntax.

    Both spellings are tried, because the break either kept the hyphen it
    happened on or swallowed it. The line the second piece came from is marked
    consumed so it cannot go on to open a row of its own.

    What is returned is the syntax's spelling of the rejoined name, never the
    table's. The difference is not cosmetic: a description wrapping into the
    values column can spell a name the card has already used - "(Trunk)
    интерфейса" over the row for trunk-number - and rejoining it produces
    "Trunk-number", which is a different string from "trunk-number" and so
    slips past the list of rows already claimed and opens a second one.
    """
    for head in TOKEN_RE.findall(body[index]):
        if vocabulary.knows(head):
            continue
        for offset in range(1, BROKEN_NAME_REACH + 1):
            if index + offset >= len(body) or index + offset in consumed:
                continue
            for tail in TOKEN_RE.findall(body[index + offset]):
                for candidate in (f"{head}{tail}", f"{head}-{tail}"):
                    spelling = vocabulary.spell(candidate)
                    if spelling is not None and spelling not in seen:
                        consumed.add(index + offset)
                        return spelling
    return None


# The shortest fragment worth completing, on either side of the break. Two
# letters name nothing: "in" opens onto half the vocabulary of a switch, and a
# tail that short is found inside some Russian word on every card.
MIN_FRAGMENT = 3


def _welded_name(
    body: list[str],
    index: int,
    indent: int,
    edge: int | None,
    vocabulary: _Vocabulary,
    seen: set[str],
) -> str | None:
    """A name whose tail the conversion buried inside a neighbouring word.

    The row for "interface-number" arrives as "interface-" in the name column
    and "number" stuck to the end of "Switchnumber" over in the values column,
    where no rule about tokens will ever find it: the tail is not a word on the
    page any more. So the question is turned around. The fragment in the name
    column is asked what it could have been, and the syntax answers - it knows
    the names this card uses, so a fragment has only a handful of completions
    and usually one. The completion is then only accepted if the letters it is
    missing are still somewhere in the row, buried or not, which is what keeps
    the syntax from simply donating a name the table never mentioned.
    """
    stripped = body[index].lstrip()
    position = len(body[index]) - len(stripped)
    if position > indent + NAME_COLUMN_SLACK or (edge is not None and position >= edge):
        return None
    match = TOKEN_RE.match(stripped)
    if match is None:
        return None
    head = match.group(0)
    if len(head) < MIN_FRAGMENT or vocabulary.knows(head):
        return None
    # The row as it stands on the page: the rest of the fragment's own line,
    # plus the lines the values column wrapped onto.
    rest = " ".join([stripped[match.end() :], *body[index + 1 : index + 1 + BROKEN_NAME_REACH]])
    for candidate in vocabulary.completions(head):
        tail = candidate[len(head) :].lstrip("-")
        if len(tail) < MIN_FRAGMENT or candidate in seen:
            continue
        if any(tail in word for word in TOKEN_RE.findall(rest)):
            return candidate
    return None


def _welded_head(
    line: str, indent: int, edge: int | None, vocabulary: _Vocabulary, seen: set[str]
) -> str | None:
    """A name with its own description welded onto it, gutter and all.

    Where ``_welded_name`` has a fragment of a name and goes looking for the
    rest, this has the whole name and something else stuck to it: the column
    gap vanished in conversion and the row arrived as "sendIGMP Protocol Send
    Message Debug Information", "nameAAA Billing Method Name in String Form",
    "process-idOSPF instance number". The name is a prefix, and the syntax says
    which prefix.

    Two things keep that from donating a name to any word that happens to open
    with one. The longest known prefix wins, so "process-idOSPF" opens the row
    for process-id rather than the one for process, which the same card also
    uses. And what remains after the prefix must start with a capital: the
    weld happened at a gutter, and across a gutter stands the description,
    which this manual begins with a capital letter. An ordinary word that
    merely starts with a parameter's name continues in lower case and is left
    alone.
    """
    stripped = line.lstrip()
    position = len(line) - len(stripped)
    if position > indent + NAME_COLUMN_SLACK or (edge is not None and position >= edge):
        return None
    match = TOKEN_RE.match(stripped)
    if match is None or vocabulary.knows(match.group(0)):
        return None
    token = match.group(0)
    for length in range(len(token) - 1, MIN_FRAGMENT - 1, -1):
        if not token[length].isupper():
            continue
        spelling = vocabulary.spell(token[:length])
        if spelling is not None and spelling in vocabulary.tokens and spelling not in seen:
            return spelling
    return None
