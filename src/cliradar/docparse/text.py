"""Small things every layer of this reader needs.

Nothing here knows what a card or a table is. It is the vocabulary the other
three modules share: how a token is spelled, what the conversion did to a word
it broke across a column edge, and how to put a wrapped line back together.
"""

from __future__ import annotations

import re

# A word the conversion broke across a column edge, hyphen and gap and all.
HYPHENATION_RE = re.compile(r"(\w)-\s+(?=\w)")

# An em dash standing alone in a column, meaning "nothing here". It is not
# prose, but joining a wrapped row puts it in the middle of the sentence -
# "Команда не имеет" and "параметров" sit on two lines with the values
# column's dash between them - and the sentence then reads "не имеет —
# параметров", which no phrase test recognises. Dropped before the joined
# block is read.
COLUMN_FILLER_RE = re.compile(r"(?<!\S)[—–-]+(?!\S)")

# A token of a syntax line. Leading digits are part of the token
# ("10gigaethernet"), and the suffixes a manual hangs on a parameter ("/M" for a
# prefix length) travel with it so the table lookup can try both spellings.
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def stem(token: str) -> str:
    """A name with the ordinal a syntax line hangs on it removed.

    A table describes one parameter where the syntax spells out four of them:
    the row is "next-hop-address" and the line reads
    "next-hop-address1 [next-hop-address2 ... next-hop-address4]". The ordinal
    is the syntax counting repetitions, not part of the name, so both sides are
    compared with it off.
    """
    return token.rstrip("0123456789")


def join_wrapped(lines: list[str]) -> list[str]:
    """Rejoin a syntax line the conversion broke mid-token.

    A line ending in a hyphen was cut inside a word: "dst-" + "ip" is one
    parameter, and left apart it becomes the command "dstip", which no device
    has ever offered. There are only a couple in a whole manual, and each one is
    a permanent phantom in the catalog.
    """
    joined: list[str] = []
    for line in lines:
        if joined and joined[-1].rstrip().endswith("-"):
            joined[-1] = joined[-1].rstrip() + line.strip()
            continue
        joined.append(line)
    return joined


# Russian anywhere in a syntax line. A command form in this manual is Latin
# throughout; Cyrillic in it means the conversion mixed prose into the block.
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
# The manual's own way of writing "form - what it does", and the ellipsis it
# uses when it means "and the rest of the forms above".
PROSE_TAIL_RE = re.compile(r"\s+[—–]\s+")
ELLIPSIS_RE = re.compile(r"\.\.\.|…")


def command_form(line: str) -> str | None:
    """The command a syntax line states, or None if it states none.

    The syntax block is not only syntax. It carries the headings that say which
    mode the forms below belong to ("Глобальный вид конфигурации:"), it carries
    prose describing a form after a dash, and once it carries nothing but a
    pointer to a list somewhere else ("(См. перечень команд выше.)"). All three
    used to arrive in the catalog as commands.

    A heading is not simply dropped, because the manual sometimes puts the form
    on the same line as the heading that introduces it - "Глобальный вид
    конфигурации: no line vty vty-number" is a command and would be lost with
    it. What survives is the Latin remainder.
    """
    text = line.strip()
    if not text:
        return None
    head, colon, tail = text.rpartition(": ")
    if colon and CYRILLIC_RE.search(head):
        text = tail.strip()
    text = PROSE_TAIL_RE.split(text, maxsplit=1)[0].strip()
    if not text or CYRILLIC_RE.search(text) or ELLIPSIS_RE.search(text):
        return None
    return text
