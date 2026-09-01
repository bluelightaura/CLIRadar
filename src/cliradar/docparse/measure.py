"""Measure this reader against a real manual: ``python -m cliradar.docparse.measure DOC``.

A reader of damaged text cannot be judged by whether it runs. It has to be
judged by how much of a document it accounts for, and that number has to be
reproducible by anyone, months later, without the session that produced it -
which is why this lives in the repository rather than in a scratch file.

Two questions are asked, and they are different. Form: how many cards were read
at all - a table that yielded rows, or a card that said it has no parameters.
Content: on what evidence, because a parameter settled by the last rule in the
ladder ("the row named it and described it in neither way") is a parameter the
reader guessed at.

Nothing here is imported by the application. It is a measuring stick.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

from . import docx
from .cards import is_card_reference, split_cards
from .catalog import UNUSED, Catalog, build_catalog
from .profile import Profile, available

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# A placeholder that states the device's own domain, as opposed to one that
# falls back to the parameter's name. The distinction was worth making exact:
# counting every placeholder with a hyphen in it also counted "<file-name>",
# and a rule that turned "<level-value>" into "<0-15>" for 165 rows moved that
# number by 24, which reads as a rule that did almost nothing.
NUMERIC_DOMAIN = re.compile(r"<\d+-\d+>")


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def report(catalog: Catalog, out=sys.stdout) -> dict[str, int]:
    """Print the measurement and hand back the counts, for tests to assert on."""
    commands = catalog.commands
    total = len(commands)
    print(f"document      : {catalog.source}", file=out)
    print(f"profile       : {catalog.profile}", file=out)
    print(f"cards         : {total}", file=out)
    print(f"recognised    : {catalog.recognised}", file=out)
    if not total:
        return {"cards": 0}

    read = sum(1 for r in commands if r.table_read)
    none = sum(1 for r in commands if r.takes_no_parameters)
    rows = [p for r in commands for p in r.parameters]
    dirty_names = [p.name for p in rows if CYRILLIC.search(p.name) or " " in p.name]
    unread = [r.command for r in commands if not r.table_read]

    print("\nform", file=out)
    print(f"  read completely  : {read} ({_percent(read, total)})", file=out)
    print(f"    with a table   : {read - none} ({_percent(read - none, total)})", file=out)
    print(f"    'no parameters': {none} ({_percent(none, total)})", file=out)
    print(f"  unreadable table : {len(unread)} ({_percent(len(unread), total)})", file=out)
    print(f"  damaged names    : {len(dirty_names)} of {len(rows)} rows", file=out)
    if unread[:5]:
        print(f"  unreadable       : {', '.join(unread[:5])}", file=out)

    print("\ncontent", file=out)
    reasons = collections.Counter(p.reason for p in rows)
    for reason, count in reasons.most_common():
        print(f"  {reason:<18}: {count} ({_percent(count, len(rows))})", file=out)
    ranged = sum(1 for p in rows if NUMERIC_DOMAIN.fullmatch(p.written))
    print(f"\n  numeric domains    : {ranged} ({_percent(ranged, len(rows))} of rows)", file=out)
    unused = sum(1 for p in rows if p.kind == UNUSED)
    print(f"  rows no syntax uses: {unused} ({_percent(unused, len(rows))})", file=out)

    return {
        "cards": total,
        "read": read,
        "no_parameters": none,
        "unread": len(unread),
        "rows": len(rows),
        "damaged_names": len(dirty_names),
        "guessed": reasons.get("default", 0),
    }


def _read(document: Path) -> tuple[str, Profile | None]:
    """The document as text, and the profile that had to be chosen to get it.

    A manual that arrives already marked up is simply read, and the profile is
    settled afterwards by the catalog. One that arrives as a .docx cannot be:
    telling a block title from a line of prose in a paged conversion needs the
    list of block names before there is any text to judge, so the profiles that
    carry such a list are tried in turn and the first the document earns wins.
    A profile without one cannot read a .docx and is not offered the chance.
    """
    if document.suffix.lower() != ".docx":
        return document.read_text(encoding="utf-8", errors="replace"), None
    fallback = ""
    for candidate in available():
        if not candidate.sections:
            continue
        text = docx.read(document, candidate)
        fallback = fallback or text
        if is_card_reference(split_cards(text, candidate), candidate):
            return text, candidate
    return fallback, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document", type=Path, help="the manual to read")
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="FILE",
        help="also write the catalog, for diffing against a later run",
    )
    args = parser.parse_args(argv)

    text, profile = _read(args.document)
    catalog = build_catalog(text, source=args.document.name, profile=profile)
    report(catalog)
    if args.json:
        args.json.write_text(catalog.to_json(), encoding="utf-8")
        print(f"\ncatalog written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
