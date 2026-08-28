"""Harvest real instance values from the running configuration for probing.

The safe probe policy refuses to invent parameter values, so contexts that are
entered by instance ("interface 10ge1/0/10", "vlan 100", "line vty 1") stay
unscanned unless the operator hand-feeds samples. But the audit already reads
the running configuration - and every top-level line in it is an instance that
EXISTS on the device right now. Typing such a line back changes nothing: it
re-enters (or re-states) what is already configured. That makes the running
config a source of probe values that is both safe and, by construction, never
hallucinated - the device itself supplied them.

This module is pure: text in, candidate entry lines out, grouped by their head
verb. The mode scanner decides where (and whether) each is worth typing - the
denylist, the mode-entry allowlist and the per-context command surface still
apply there.
"""

from __future__ import annotations

import re

from .runconfig import parse_config

_DIGITS_RE = re.compile(r"\d+")

# How many real lines to keep per distinct shape. Contexts fold instance
# numbers ("interface 10ge1/0/1" and .../2 are one context), so one line per
# shape is enough to open it; a second covers a flaky first try.
_MAX_PER_SHAPE = 2


def harvest_probe_entries(
    config_text: str, command: str = ""
) -> dict[str, list[str]]:
    """Top-level running-config lines as probe entries, grouped by head verb.

    Only top-level lines are taken: a nested line lives inside a view and would
    be typed out of place. Lines are deduplicated by their digit-folded shape,
    so a switch with hundreds of interfaces contributes a couple of lines, not
    hundreds of probes - the scanner's context folding treats them as one
    context anyway.
    """
    entries: dict[str, list[str]] = {}
    shapes_seen: dict[str, int] = {}
    for line in parse_config(config_text, command):
        if len(line.path) != 1 or line.verbatim:
            continue
        text = line.text.strip()
        if not text:
            continue
        shape = _DIGITS_RE.sub("*", text)
        count = shapes_seen.get(shape, 0)
        if count >= _MAX_PER_SHAPE:
            continue
        shapes_seen[shape] = count + 1
        head = text.split()[0].lower()
        entries.setdefault(head, []).append(text)
    return entries
