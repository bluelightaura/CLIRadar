"""Fold an audit's flat context list into the tree the operator navigates.

An audit records its contexts as a flat list (see ``ModeScanReport.to_dict``):
each entry carries a ``/``-joined ``name`` that already encodes the path from
the root, the prompt ``fingerprint`` that identifies the context, the
``entry_path`` of commands that reach it, and the command and query counts the
scan measured. This module rebuilds the tree from that list, splits it into the
exec and config halves the menu shows as its two top blocks, and estimates how
long re-running any subtree would take from the query counts already recorded.

Everything here is pure - it reads the saved numbers and returns a tree, with no
device and no I/O - so the menu can draw it instantly from a stored catalog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# Per-query wall-clock, by transport. No latency is measured during a scan yet,
# so these are deliberately coarse: they exist to turn a query count into an
# "order of minutes" a person can weigh, not to be exact. Once a real run is
# timed the constants (or a measured average) replace these. [[live-validation]]
SECONDS_PER_QUERY = {"ssh": 0.15, "telnet": 0.30}
_DEFAULT_SECONDS_PER_QUERY = 0.15


@dataclass
class ContextNode:
    """One CLI context and the contexts reachable by descending into it."""

    name: str  # full "/"-joined path from the root, e.g. "root/system-view/vrf"
    fingerprint: str | None
    entry_path: tuple[str, ...]
    commands: int  # commands recorded in THIS context alone
    queries: int  # help queries THIS context cost the last scan
    complete: bool
    children: list["ContextNode"] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The last path segment - what a tree row shows, e.g. "vrf"."""
        return self.name.rsplit("/", 1)[-1]

    @property
    def is_config(self) -> bool:
        """A config context wears a parenthesised prompt, e.g. "(config-vrf)#".

        The root and any exec context show a bare "#"/">" instead, so the prompt
        alone separates the two top blocks the menu draws without a hard-coded
        list of mode names - the point of a firmware-independent tool.
        """
        return bool(self.fingerprint) and self.fingerprint.startswith("(")


def build_context_tree(contexts: Iterable[Mapping[str, object]]) -> ContextNode:
    """Rebuild the context tree from an audit's flat ``contexts`` list.

    Parenting is by name path, not by fingerprint: two contexts can share a
    prompt, but a name like "root/system-view/vrf" names exactly one place, and
    its parent is the same string with the last segment removed. A context whose
    named parent is absent from the list is attached to the nearest ancestor
    that is present, so a partial scan still yields a single connected tree.
    """
    nodes: dict[str, ContextNode] = {}
    order: list[str] = []
    for entry in contexts:
        name = str(entry.get("name") or "root")
        fingerprint = entry.get("fingerprint")
        node = ContextNode(
            name=name,
            fingerprint=str(fingerprint) if fingerprint is not None else None,
            entry_path=tuple(str(step) for step in entry.get("entry_path") or ()),
            commands=int(entry.get("commands") or 0),
            queries=int(entry.get("queries") or 0),
            complete=bool(entry.get("complete")),
        )
        if name not in nodes:
            order.append(name)
        nodes[name] = node

    root_name = _root_name(order)
    root = nodes.get(root_name) or ContextNode(root_name, None, (), 0, 0, False)
    nodes.setdefault(root_name, root)

    for name in order:
        if name == root_name:
            continue
        parent = _nearest_present_parent(name, nodes, root_name)
        nodes[parent].children.append(nodes[name])
    return nodes[root_name]


def _root_name(order: list[str]) -> str:
    """The shortest-path name is the root; "root" by convention, else fallback."""
    if not order:
        return "root"
    if "root" in order:
        return "root"
    return min(order, key=lambda name: (name.count("/"), name))


def _nearest_present_parent(
    name: str, nodes: Mapping[str, ContextNode], root_name: str
) -> str:
    """Walk the name path upward to the first ancestor that was actually scanned."""
    parent = name.rsplit("/", 1)[0] if "/" in name else root_name
    while parent != root_name and parent not in nodes:
        parent = parent.rsplit("/", 1)[0] if "/" in parent else root_name
    return parent if parent in nodes else root_name


def split_top_blocks(root: ContextNode) -> tuple[list[ContextNode], list[ContextNode]]:
    """Partition the root's children into the exec and config top blocks.

    The two blocks the menu shows are not two fixed subtrees but two kinds: any
    child that opens a parenthesised (config) prompt heads the config block, and
    everything else - the root's own exec neighbourhood - heads the exec block.
    The root itself belongs to neither list; it is the frame both hang under.
    """
    exec_block = [child for child in root.children if not child.is_config]
    config_block = [child for child in root.children if child.is_config]
    return exec_block, config_block


def subtree_commands(node: ContextNode) -> int:
    """Commands recorded in this context and everything reachable below it."""
    return node.commands + sum(subtree_commands(child) for child in node.children)


def subtree_queries(node: ContextNode) -> int:
    """Help queries this context and its whole subtree cost the last scan."""
    return node.queries + sum(subtree_queries(child) for child in node.children)


def subtree_contexts(node: ContextNode) -> int:
    """How many contexts a "run this block" would step through, this one included."""
    return 1 + sum(subtree_contexts(child) for child in node.children)


def estimate_seconds(node: ContextNode, transport: str = "ssh") -> float:
    """Rough wall-clock to re-run this subtree, from its recorded query count."""
    per_query = SECONDS_PER_QUERY.get(transport.lower(), _DEFAULT_SECONDS_PER_QUERY)
    return subtree_queries(node) * per_query


def format_duration(seconds: float) -> str:
    """A compact "~2m30s" / "~45s" / "<1s" badge for a tree row."""
    if seconds < 1:
        return "<1s"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes == 0:
        return f"~{secs}s"
    if secs == 0:
        return f"~{minutes}m"
    return f"~{minutes}m{secs:02d}s"
