"""The blocks a switch is expected to have, and how far each one is known.

The map browser used to be empty until an audit had run, and after a run it
showed only what was found - so "is there no VRF block, or did we never look?"
had no answer on screen. This module supplies both halves of that answer:

* a small blueprint of the configuration blocks nearly every switch exposes
  (interfaces, VLANs, VRFs, line/user-interface, routing, ...), drawn straight
  away so the tree has shape before the first scan, and
* the state of a node, which says how much of it is actually known:

    ``unknown``  ○  never looked - a blueprint row, or a context with nothing
                    recorded in it yet
    ``topped``   ⊙  the top was taken (a skim pass) - the commands directly in
                    the context are known, what they open is not
    ``parsed``   ✓  crawled to completion
    ``absent``   ·  looked for and not there: the device refused the entry

The blueprint is deliberately vendor-plural: each block lists the head verbs
different firmwares use for the same idea ("line" and "user-interface", "mlag"
and "vpc"), because a row is only useful if it matches whatever the device in
front of the operator happens to call it. Nothing here talks to a device - it is
a table plus two pure functions, so the menu can draw states instantly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

UNKNOWN = "unknown"
TOPPED = "topped"
PARSED = "parsed"
ABSENT = "absent"

# The glyph each state wears in the tree. Kept here, next to the states, so the
# renderer cannot drift from the vocabulary.
STATE_MARKS: dict[str, str] = {
    UNKNOWN: "○",
    TOPPED: "⊙",
    PARSED: "✓",
    ABSENT: "·",
}


@dataclass(frozen=True)
class Block:
    """One expected configuration block and the verbs that open it."""

    label: str
    verbs: frozenset[str]

    def matches(self, verb: str) -> bool:
        return verb.lower() in self.verbs


# One row per idea, not per vendor spelling. Order is the order they are drawn:
# the blocks an operator reaches for first come first.
BLOCKS: tuple[Block, ...] = (
    Block("interface", frozenset({"interface", "int"})),
    Block("vlan", frozenset({"vlan"})),
    Block("vrf", frozenset({"vrf", "ip-vpn-instance", "vpn-instance"})),
    Block("mlag", frozenset({"mlag", "m-lag", "vpc", "stack"})),
    Block("line", frozenset({"line", "user-interface"})),
    Block("router", frozenset({"router", "bgp", "ospf", "isis", "rip"})),
    Block("acl", frozenset({"acl", "access-list", "ip-access-list"})),
    Block("route-map", frozenset({"route-map", "route-policy"})),
    Block("prefix-list", frozenset({"prefix-list", "ip-prefix"})),
    Block("aaa", frozenset({"aaa"})),
    Block("stp", frozenset({"stp", "spanning-tree"})),
    Block("snmp", frozenset({"snmp", "snmp-server", "snmp-agent"})),
    Block("ntp", frozenset({"ntp", "ntp-service"})),
)


def node_state(commands: int, complete: bool) -> str:
    """How far a recorded context is known, from what the audit wrote down.

    ``complete`` is the scan's own verdict that it crawled the context to the
    end. Without it, commands recorded still mean the top was taken - which is
    exactly what a skim pass leaves behind - and nothing recorded means the
    context was named by a probe but never opened.
    """
    if complete:
        return PARSED
    return TOPPED if commands > 0 else UNKNOWN


def blueprint_state(block: Block, rejected: Iterable[str]) -> str:
    """The state of a blueprint row that is not (yet) a node in the map.

    ``rejected`` are head verbs the scan typed and the device refused: that is
    the evidence for "this device does not have this block", as opposed to "we
    never asked", which is every other case.
    """
    return ABSENT if any(block.matches(verb) for verb in rejected) else UNKNOWN


def missing_blocks(
    found: Iterable[str], rejected: Iterable[str] = ()
) -> list[tuple[Block, str]]:
    """Blueprint blocks the map does not already show, with their state.

    A block is "already shown" when some context in the map was entered by one
    of its verbs - the operator can see the real thing, so drawing a template
    row for it as well would only duplicate it.
    """
    labels = [label.lower() for label in found]
    refused = [verb.lower() for verb in rejected]
    missing: list[tuple[Block, str]] = []
    for block in BLOCKS:
        if any(block.matches(_head(label)) for label in labels):
            continue
        missing.append((block, blueprint_state(block, refused)))
    return missing


def rejected_verbs(probes: Iterable[Mapping[str, object]]) -> set[str]:
    """Head verbs a scan typed and the device answered with an error.

    A rejected probe is the only evidence the tool has that a block is not on
    this device: the command was offered by the help surface, typed, and refused.
    Anything else (never probed, probe skipped, session lost) leaves the block
    unknown rather than absent, which is why only "rejected" counts here.
    """
    refused: set[str] = set()
    for probe in probes:
        if str(probe.get("outcome") or "") != "rejected":
            continue
        command = str(probe.get("command") or "").strip()
        if command:
            refused.add(_head(command))
    return refused


def _head(text: str) -> str:
    """The first word of a command or a context label, lower-cased."""
    return text.strip().split(" ")[0].lower() if text.strip() else ""
