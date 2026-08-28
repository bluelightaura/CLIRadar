"""One small report per configuration block, instead of one huge catalog.

The full catalog answers "what can this device do"; it is thousands of lines and
nobody reads it to settle a question about VLANs. These reports answer the
narrower question people actually ask - "what does the vlan block look like on
this box, and how much of it do we actually know" - one file per block, in the
order an operator thinks about them.

Everything here is pure: it reads a catalog that has already been written and
returns markdown. Nothing talks to a device, so a report can be regenerated from
a stored catalog at any time, and the numbers in it are the scan's own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .blueprint import PARSED, TOPPED, node_state

# How many commands to spell out before collapsing the tail into a count. A
# block report is meant to be read; an interface block with 4000 entries is a
# catalog, and the catalog is where the full list already lives.
MAX_LISTED = 200

_STATE_WORDS = {
    PARSED: "parsed - crawled to completion",
    TOPPED: "top only - the discovery pass took the surface, not the depth",
}
_UNKNOWN_WORD = "not looked at - the context is named but was never opened"


def block_label(context_name: str) -> str:
    """The last path segment of a context name: "root/system-view/vlan" -> "vlan"."""
    return context_name.rsplit("/", 1)[-1]


def block_contexts(contexts: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The contexts worth their own report: every config block below the top.

    A config context is the one whose prompt is parenthesised - the same test
    the map browser splits its two halves by. The config root itself ("(config)#"
    reached by `system-view`) is skipped: it is the container, not a block.
    """
    by_name = {str(item.get("name") or ""): item for item in contexts}
    blocks: list[Mapping[str, Any]] = []
    for name, item in by_name.items():
        fingerprint = str(item.get("fingerprint") or "")
        if not fingerprint.startswith("("):
            continue
        parent = name.rsplit("/", 1)[0] if "/" in name else ""
        parent_fp = str((by_name.get(parent) or {}).get("fingerprint") or "")
        if not parent_fp.startswith("("):
            continue  # the config root itself, whose parent is an exec context
        blocks.append(item)
    return sorted(blocks, key=lambda item: str(item.get("name") or ""))


def commands_in(
    catalog: Mapping[str, Any], context: Mapping[str, Any]
) -> list[str]:
    """Commands recorded inside one context, as a person would type them.

    The catalog keys every command by its full path from the root, which is the
    context's entry path followed by the command itself - so the block's own
    commands are exactly those carrying that prefix.
    """
    prefix = " ".join(str(step) for step in context.get("entry_path") or ())
    found: list[str] = []
    for item in catalog.get("commands") or []:
        if not isinstance(item, Mapping):
            continue
        command = str(item.get("command") or "")
        if prefix and not command.startswith(prefix + " "):
            continue
        if "cli" not in (item.get("source") or []):
            continue  # documented-only entries belong to the compare report
        found.append(command)
    return sorted(found)


def _gaps(context: Mapping[str, Any]) -> list[str]:
    """Why a block is not complete, in the scan's own words."""
    notes: list[str] = []
    skipped = context.get("skipped_parameters") or []
    if skipped:
        joined = ", ".join(str(item) for item in list(skipped)[:6])
        notes.append(f"{len(skipped)} parameter(s) with no sample: {joined}")
    if context.get("skipped_depth"):
        notes.append(f"{context['skipped_depth']} branch(es) cut by the depth limit")
    if context.get("skipped_denied"):
        notes.append(f"{context['skipped_denied']} command(s) held back by the denylist")
    derived = int(context.get("derived_nodes") or 0)
    if derived:
        verified = int(context.get("derived_verified") or 0)
        notes.append(f"{derived} node(s) copied from an identical branch, {verified} re-checked")
    mismatched = context.get("derived_mismatched") or []
    if mismatched:
        notes.append(f"{len(mismatched)} copied branch(es) disagreed when re-checked")
    if context.get("derived_truncated"):
        notes.append("a copied branch was truncated")
    return notes


def render_block_report(
    catalog: Mapping[str, Any],
    context: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] = (),
) -> str:
    """One block's report as markdown: what it is, how known it is, what is in it."""
    name = str(context.get("name") or "")
    label = block_label(name)
    entry = " → ".join(str(step) for step in context.get("entry_path") or ()) or "-"
    commands = commands_in(catalog, context)
    state = node_state(int(context.get("commands") or 0), bool(context.get("complete")))
    lines = [
        f"# {label}",
        "",
        f"- context: `{name}`",
        f"- entered by: `{entry}`",
        f"- state: {_STATE_WORDS.get(state, _UNKNOWN_WORD)}",
        f"- commands recorded here: {int(context.get('commands') or 0)}",
        f"- help queries spent: {int(context.get('queries') or 0)}",
    ]
    if context.get("recoveries"):
        lines.append(f"- context recoveries during the scan: {context['recoveries']}")
    if children:
        names = ", ".join(block_label(str(child.get("name") or "")) for child in children)
        lines.append(f"- blocks opened from here: {len(children)} ({names})")

    gaps = _gaps(context)
    if gaps:
        lines += ["", "## What is missing", ""]
        lines += [f"- {note}" for note in gaps]
    elif state != PARSED:
        lines += ["", "## What is missing", "", "- the block was not crawled to the end"]

    lines += ["", f"## Commands ({len(commands)})", ""]
    if not commands:
        lines.append("_none recorded in this context yet_")
    else:
        lines += [f"- `{command}`" for command in commands[:MAX_LISTED]]
        if len(commands) > MAX_LISTED:
            lines.append(f"- … and {len(commands) - MAX_LISTED} more (see the catalog)")
    return "\n".join(lines) + "\n"


def render_block_reports(catalog: Mapping[str, Any]) -> dict[str, str]:
    """Every config block in a catalog, as {file name: markdown}.

    Blocks that share a label (two firmwares, two paths to "vlan") are kept
    apart by their full context path, so one never silently overwrites another.
    """
    scan = catalog.get("scan")
    contexts = scan.get("contexts") if isinstance(scan, Mapping) else None
    if not isinstance(contexts, list):
        return {}
    blocks = block_contexts([item for item in contexts if isinstance(item, Mapping)])
    reports: dict[str, str] = {}
    used: set[str] = set()
    for block in blocks:
        name = str(block.get("name") or "")
        children = [
            item
            for item in contexts
            if isinstance(item, Mapping) and str(item.get("name") or "").startswith(name + "/")
        ]
        stem = _safe_name(block_label(name))
        if stem in used:
            stem = _safe_name(name)
        used.add(stem)
        reports[f"{stem}.md"] = render_block_report(catalog, block, children)
    return reports


def _safe_name(label: str) -> str:
    """A file name that cannot escape its directory or surprise a shell."""
    kept = [char if char.isalnum() or char in "-_" else "-" for char in label.lower()]
    return "".join(kept).strip("-") or "block"
