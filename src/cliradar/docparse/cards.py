"""A manual written as one card per command, cut into those cards.

Some vendor references are not prose with commands scattered through them: they
are a card per command, each carrying the same blocks - a syntax listing, a
parameter table, a command mode, a worked example. Read line by line, such a
document poisons the catalog. The parameter table's first column arrives as a
thousand one-word "commands" (acl-name, acct-port, and the hyphenated
wreck accounting-), the worked examples arrive as commands carrying somebody
else's addresses (neighbor 10.18.2.111 next-hop-local), and the prose
bullets arrive with the word-wrap damage the conversion left in them
(interfacenumber for interface-number).

Read as cards, all three disappear at once: only the syntax listing is a command
surface, and everything else is either evidence about it or noise.

This module does the cutting and decides whether a document deserves to be cut
this way at all. What the parameter block means is table's job, and what the
syntax tokens turn out to be is marking's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .profile import Profile, default
from .table import parameter_rows
from .text import COLUMN_FILLER_RE, HYPHENATION_RE, TOKEN_RE, command_form, join_wrapped

# Which words head a card, name its blocks and say it has no parameters is a
# property of the manual, not of this reader, and lives in a profile - see
# profile.py and profiles/*.json. Two facts are kept here because they are
# properties of the file format rather than of the document: a block title is
# printed in bold, and a fenced region is content whatever it looks like.
#
# The distinction the "no parameters" patterns exist to draw is worth stating
# once more, because everything downstream rests on it: a card that says it has
# none is read completely and every token of it is a keyword, while a table
# this reader could not read is a failure and must be counted as one.
SECTION_RE = re.compile(r"^\*\*(?P<name>.+?)\*\*\s*$")
FENCE_RE = re.compile(r"^\s*```")


@dataclass
class Card:
    """One command's card: its heading, and the two blocks worth reading."""

    command: str
    syntax: list[str] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    # Whether the card carried a parameter block at all, which is not the same
    # as its having produced rows: an unreadable table must not be mistaken for
    # a command that takes no parameters.
    had_parameter_block: bool = False
    # Whether that block said, in so many words, that there are none. This is
    # the other half of the distinction: with it a card of pure keywords is a
    # complete read, and without it an empty table is a failure worth counting.
    takes_no_parameters: bool = False
    # Whether either block was printed under this heading at all - asked of the
    # heading, not of what the block held. Kept apart from the two flags above
    # because it answers a different question: those two say what a card
    # turned out to contain, this one says whether there was a card here.
    had_block: bool = False

    @property
    def structured(self) -> bool:
        return bool(self.syntax and self.had_parameter_block)

    @property
    def nothing_to_describe(self) -> bool:
        """Does the card's syntax hold a token a parameter table could be about?

        A command whose every form is its own name - "laser
        bias-current-threshold auto" - has no parameters to describe, whatever
        its parameter block turned out to say. One such block is a sentence
        about the default behaviour with no table in it at all, and reading no
        rows out of it was being counted as a table this reader failed on. It
        is not a failure: there was nothing in it to find, and no row it could
        have carried would have changed a single token of the marked syntax.

        This is asked only of a card that yielded no rows, and it decides how
        that silence is counted - never what the card becomes.
        """
        named = set(TOKEN_RE.findall(self.command))
        return not any(
            token not in named and token != "no"  # nosec B105 - CLI negation word
            for line in self.syntax
            for token in TOKEN_RE.findall(line)
        )


def says_no_parameters(lines: list[str], profile: Profile | None = None) -> bool:
    """Does this block state that there are none, rather than fail to list them?

    The distinction is the whole point of asking: a card that says so is read
    completely, and every token of its syntax is a keyword; a table this reader
    could not read is a failure, and must be counted as one rather than quietly
    promoted into a command of pure keywords.
    """
    profile = profile or default()
    if any(profile.no_parameters_word.match(line) for line in lines if line.strip()):
        return True
    # The filler goes first. A word broken across the column edge can have the
    # dash land inside the break - "не принимает па-", "—", "раметров" - and
    # mending the word before the dash is gone mends nothing.
    joined = COLUMN_FILLER_RE.sub(" ", " ".join(lines))
    joined = HYPHENATION_RE.sub(r"\1", joined)
    return bool(profile.no_parameters_phrase.search(joined))


def split_cards(text: str, profile: Profile | None = None) -> list[Card]:
    """Every card in the document, with its syntax and parameter blocks.

    Only fenced content inside the two blocks is kept. A section heading that
    appears inside a fence is content rather than a heading, so the fence is
    tracked first and the heading tests only run outside one.
    """
    profile = profile or default()
    cards: list[Card] = []
    current: Card | None = None
    raw_parameters: list[str] = []
    section = ""
    in_fence = False

    def close() -> None:
        if current is None:
            return
        # The syntax is joined first because the table reader leans on it: a
        # name broken across a column edge is only recoverable against a
        # syntax line that is itself whole.
        current.syntax[:] = join_wrapped(current.syntax)
        current.parameters.update(
            parameter_rows(raw_parameters, current.syntax, profile)
        )
        # Asked only of a block that yielded nothing. A table that listed
        # parameters has answered the question already, and a description
        # mentioning that some other form of the command takes none must not
        # be allowed to overrule the rows standing above it.
        if not current.parameters and says_no_parameters(raw_parameters, profile):
            current.takes_no_parameters = True

    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            heading = profile.heading.match(line)
            if heading:
                close()
                current = Card(command=heading.group("command").strip())
                cards.append(current)
                raw_parameters = []
                section = ""
                continue
            marker = SECTION_RE.match(line)
            if marker:
                section = marker.group("name").strip()
                if current is not None and (
                    section in profile.syntax_sections
                    or section in profile.parameter_sections
                ):
                    current.had_block = True
            continue
        if current is None:
            continue
        if section in profile.syntax_sections:
            form = command_form(line)
            if form:
                current.syntax.append(form)
        elif section in profile.parameter_sections:
            current.had_parameter_block = True
            raw_parameters.append(line)
    close()
    # A line can look like a heading without one standing there. This manual
    # illustrates its own typographic conventions in the front matter by
    # printing three sample card headings, and read literally they open three
    # cards that carry nothing - while the commands they name have real cards
    # further down. Declining them is not the same as hiding a failed read: a
    # heading under which the document printed neither block is a line that
    # resembles a heading, and counting it as a card this reader could not
    # manage would be the dishonest option, not the strict one.
    #
    # The rule is the document's, not a guess: of 1771 headings in the Centec
    # reference exactly these 3 carry no block, and of 1795 in the L3200
    # reference none do.
    return [card for card in cards if card.had_block]


def is_card_reference(cards: list[Card], profile: Profile | None = None) -> bool:
    """Is this document regular enough to be read as cards?

    A structured read is a profile the document has to earn, not a default. The
    test is about regularity rather than volume: a prose manual that happens to
    contain a few command cards is still prose, and forcing it through this
    reader would lose everything outside them. Takes the split rather than the
    text so a caller that goes on to read the cards does not split twice - these
    documents run to megabytes.
    """
    profile = profile or default()
    if len(cards) < profile.min_cards:
        return False
    return (
        sum(1 for card in cards if card.structured) / len(cards) >= profile.min_conforming
    )
