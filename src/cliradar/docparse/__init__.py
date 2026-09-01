"""Reading a vendor manual as a catalog of commands.

A manual of the shape this package is written for prints one card per command:
a heading naming it, a **Синтаксис** block listing its forms, and a
**Параметры** table saying which of the tokens in those forms are keywords to
be typed and which are values to be supplied. Reading that structure is a
different job from reading a manual line by line - the line reader cannot tell
a syntax listing from the parameter table beside it or the worked example
beneath it - and on a document of this shape that difference is most of the
catalog.

The package is kept behind one gate. ``is_card_reference`` decides whether a
document earned this treatment, and a document that did not is handed back to
the line reader in ``docs.py`` untouched. Nothing here runs on a manual it was
not written for.

One manual arrives already marked up and another arrives as a .docx made from
a PDF; ``docx.py`` turns the second into the first rather than teaching the
rest of the package what a .docx is.

What it costs the project: the standard library, and nothing else.
"""

from __future__ import annotations

from . import docx
from .cards import Card, is_card_reference, repair_welded_tokens, split_cards
from .catalog import (
    Catalog,
    CommandRecord,
    Parameter,
    build_catalog,
    purpose_for,
    read_card,
)
from .marking import mark_card, mark_parameters, placeholder
from .profile import Profile, builtin, load
from .table import parameter_rows

__all__ = [
    "Card",
    "Catalog",
    "CommandRecord",
    "Parameter",
    "Profile",
    "build_catalog",
    "builtin",
    "docx",
    "is_card_reference",
    "load",
    "mark_card",
    "mark_parameters",
    "parameter_rows",
    "placeholder",
    "purpose_for",
    "read_card",
    "repair_welded_tokens",
    "split_cards",
]
