"""What a manual turns into: one record per command, in a form that can be diffed.

The reader's product used to be a list of strings, and a list of strings cannot
be audited. This module gives it a shape instead - command, the syntax as
printed, the syntax as marked, and every parameter with the evidence that
settled it - so that a change to a rule shows up as a diff of a few rows rather
than as a number moving by half a percent, and so that a fixture in a test can
say exactly what a card is supposed to become.

Serialisation is JSON, written with the manual's own alphabet intact: these
records are read by people checking them against a Russian document.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .cards import Card, is_card_reference, split_cards
from .marking import mark_card
from .profile import Profile, available, default


@dataclass(frozen=True)
class Parameter:
    """One row of a card's parameter table, and what the reader made of it."""

    name: str
    # "keyword" if the operator types it verbatim, "value" if it stands for
    # something supplied. "unused" is neither: the table named it and no syntax
    # line printed it, which usually means the row belongs to a form of the
    # command the card documents elsewhere.
    kind: str
    # How it is written in the marked syntax - "<1-2048>" for a value with a
    # domain, the word itself for a keyword.
    written: str
    # Which test settled it. See marking.mark_card for what each one means.
    reason: str
    description: str


@dataclass
class CommandRecord:
    """One card, read."""

    command: str
    # What the card's purpose block says the command is for. Empty when the
    # manual printed no such block under this heading.
    description: str = ""
    syntax: list[str] = field(default_factory=list)
    marked: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    # The card said in so many words that there are none. Not the same as an
    # empty table, which is a failure to read one.
    takes_no_parameters: bool = False
    # The card carried a parameter block and it yielded rows, or said there are
    # none. False marks the cards worth looking at by hand.
    table_read: bool = True


@dataclass
class Catalog:
    """Every card of one document."""

    source: str
    # Whether the document earned this reader at all. A catalog built from a
    # document that did not is kept anyway - measuring the near misses is how
    # the threshold gets set - but nothing downstream should use it.
    recognised: bool
    commands: list[CommandRecord] = field(default_factory=list)
    # Which manual's names were used to read it. Worth recording: a
    # measurement of the wrong profile looks like a measurement of a bad reader.
    profile: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "recognised": self.recognised,
            "profile": self.profile,
            "commands": [asdict(record) for record in self.commands],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)


UNUSED = "unused"


def _leading_match(purpose_line: str, command: str) -> int:
    """How many of the command's own words this line opens with.

    A manual that prints one bullet per form of the command opens each with
    that form - "no clock timezone Эта команда..." - so the bullet says which
    command it is about before it says anything else. Alternatives are written
    the way the syntax writes them, so "auth-degenerate enable|disable" has to
    count as a match for "auth-degenerate enable".
    """
    wanted = command.split()
    matched = 0
    for index, word in enumerate(purpose_line.split()):
        if index >= len(wanted):
            break
        options = {part for part in word.strip("{}[]").split("|") if part}
        if wanted[index] not in options:
            break
        matched += 1
    return matched


def purpose_for(card: Card, command: str = "") -> str:
    """What the card says this command is for, in one line.

    With no command named, or none the card's purpose block distinguishes, the
    whole block is returned joined. Naming one picks the bullet written about
    it and drops the command words the bullet opens with, which are already in
    the catalog key and would otherwise be printed twice.
    """
    lines = [line for line in card.purpose if line.strip()]
    if not lines:
        return ""
    if command:
        scored = [(_leading_match(line, command), index, line) for index, line in enumerate(lines)]
        best, _, line = max(scored)
        if best:
            return " ".join(line.split()[best:]).strip() or line.strip()
    return " ".join(lines).strip()


def read_card(card: Card, profile: Profile | None = None) -> CommandRecord:
    """One card as a record, with every table row accounted for.

    The profile travels with the card because the evidence is worded in the
    manual's own language: read with another document's vocabulary, a card is
    not misread loudly but quietly, every row falling through to a guess.

    A row is reported even when no syntax line printed it. Silence about such a
    row would hide the two ways a card goes wrong - a name recovered from
    wreckage that no longer matches the syntax, and a table describing a form of
    the command that is documented on another card - and both are worth seeing.
    """
    marked, marks = mark_card(card, profile)
    decided: dict[str, list] = {}
    for mark in marks:
        decided.setdefault(mark.token, []).append(mark)

    parameters: list[Parameter] = []
    for name, description in card.parameters.items():
        found = decided.get(name)
        if not found:
            parameters.append(Parameter(name, UNUSED, name, "unmatched-row", description))
            continue
        # One row can be decided differently on different lines - a word is a
        # keyword outside the braces and a value inside them - and each such
        # reading is kept.
        seen: set[tuple[str, str, str]] = set()
        for mark in found:
            key = (mark.kind, mark.text, mark.reason)
            if key in seen:
                continue
            seen.add(key)
            parameters.append(
                Parameter(name, mark.kind, mark.text, mark.reason, description)
            )

    read = (
        bool(card.parameters)
        or card.takes_no_parameters
        or not card.had_parameter_block
        # A block that yielded nothing about a command that has nothing to
        # describe is not an unread table. See Card.nothing_to_describe.
        or card.nothing_to_describe
    )
    return CommandRecord(
        command=card.command,
        description=purpose_for(card),
        syntax=list(card.syntax),
        marked=marked,
        parameters=parameters,
        takes_no_parameters=card.takes_no_parameters,
        table_read=read,
    )


def build_catalog(
    text: str, source: str = "", profile: Profile | None = None
) -> Catalog:
    """Read a whole document into a catalog.

    With no profile named, the shipped ones are tried in turn and the first the
    document earns is the one used - the same choice ``docs.py`` makes in
    production, made here so that a measurement reports what the application
    would actually do rather than what the default profile manages.
    """
    chosen = profile
    if chosen is None:
        for candidate in available():
            cards = split_cards(text, candidate)
            if is_card_reference(cards, candidate):
                chosen = candidate
                break
        else:
            chosen = default()
            cards = split_cards(text, chosen)
    else:
        cards = split_cards(text, chosen)
    return Catalog(
        source=source,
        recognised=is_card_reference(cards, chosen),
        commands=[read_card(card, chosen) for card in cards],
        profile=chosen.name,
    )
