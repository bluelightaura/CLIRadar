"""Scanning a CLI as a graph of contexts.

This module is the contract between the two halves of the scanner. The crawler
knows commands but nothing about sessions; the navigator knows the session but
nothing about commands. They meet here:

    crawler  --"put me in this context"-->  navigator
    crawler  <--"here is the proof"-------  navigator

The proof is the prompt fingerprint, and it is re-checked on every single help
answer rather than remembered. A device response that no longer carries the
expected prompt means the position was lost - the query is not trusted, the
navigator re-establishes the context, and the query is repeated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .crawler import CrawlLimits, CrawlProgress, CrawlResult, crawl
from .models import Catalog
from .navigator import (
    DEFAULT_PROBE_DENYLIST,
    ModeContext,
    ModeNavigator,
    NavigationError,
    fingerprint_of,
    is_probe_allowed,
    probe_order,
)
from .parser import ERROR_RE, option_kind

# Commands that usually open a context. They only set probe order, never
# correctness: an unknown platform still yields its modes, just later.
DEFAULT_MODE_HINTS: tuple[str, ...] = (
    "configure", "config", "system-view",
    "vlan", "interface", "vrf", "router", "line", "mlag", "stp", "aaa",
    "bridge-domain", "policy-map", "class-map", "filter-list", "acl",
    "ospf", "bgp", "pim", "ntp", "ptp", "dhcp", "g8032", "erps", "snmp",
)


# Catch-all placeholders: they mean "any word" and appear on unrelated
# commands, so a sample for one of them lands wherever it fits.
GENERIC_PLACEHOLDERS: frozenset[str] = frozenset(
    {"WORD", "NAME", "STRING", "LINE", "TEXT", "VALUE", "DESCRIPTION", "PASSWORD"}
)


class ContextLost(NavigationError):
    """A help answer did not come from the context it was asked in."""


@dataclass
class GuardedHelp:
    """Help queries that verify where they were answered from.

    Every contextual help response ends with the prompt, so each query doubles
    as a position check at no extra cost.
    """

    navigator: ModeNavigator
    context: ModeContext
    recoveries: int = 0
    unverified: int = 0

    def __call__(self, prefix: str) -> str:
        try:
            output = self.navigator.terminal.query_help(prefix)
        except OSError:
            output = ""  # a dead channel is a lost position, handled below
        if output and self._matches(output):
            return output

        # The answer came from somewhere else - re-establish and ask again.
        try:
            self.navigator.ensure(self.context)
        except NavigationError as error:
            raise ContextLost(
                f"context {self.context.name!r} could not be restored: {error}"
            ) from error
        self.recoveries += 1
        try:
            output = self.navigator.terminal.query_help(prefix)
        except OSError as error:
            raise ContextLost(f"the channel died while asking {prefix!r}") from error
        if not self._matches(output):
            raise ContextLost(
                f"help for {prefix!r} did not come from {self.context.fingerprint!r}"
            )
        return output

    def _matches(self, output: str) -> bool:
        fingerprint = fingerprint_of(output)
        if fingerprint is None:
            # Long help output can be cut before the prompt is echoed; that is
            # not evidence of drift, so it is counted rather than acted upon.
            self.unverified += 1
            return True
        return fingerprint == self.context.fingerprint


@dataclass
class ContextScan:
    context: ModeContext
    catalog: Catalog
    result: CrawlResult
    recoveries: int = 0
    unverified: int = 0

    @property
    def executables(self) -> list[str]:
        return sorted(
            command for command, entry in self.catalog.commands.items() if entry.executable
        )


@dataclass
class ProbeRecord:
    command: str
    context: str
    outcome: str  # "entered" | "executed" | "rejected"
    fingerprint: str | None = None


@dataclass
class ModeScanReport:
    scans: list[ContextScan] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    reopens: int = 0
    unreachable: list[str] = field(default_factory=list)
    probes_skipped: int = 0

    @property
    def commands(self) -> int:
        return sum(len(scan.catalog.commands) for scan in self.scans)

    def to_dict(self) -> dict[str, object]:
        return {
            "contexts": [
                {
                    "name": scan.context.name,
                    "fingerprint": scan.context.fingerprint,
                    "entry_path": list(scan.context.entry_path),
                    "parent": scan.context.parent,
                    "commands": len(scan.catalog.commands),
                    "complete": scan.result.complete,
                    "queries": scan.result.queries,
                    "recoveries": scan.recoveries,
                    # What stopped this context from being complete, so an
                    # incomplete result says why instead of just "false".
                    "skipped_parameters": list(scan.result.skipped_parameters),
                    "skipped_depth": scan.result.skipped_depth,
                    "skipped_denied": scan.result.skipped_denied,
                    # How much of this context was copied rather than walked,
                    # and whether the device agreed when asked.
                    "derived_nodes": scan.result.derived_nodes,
                    "derived_verified": scan.result.derived_verified,
                    "derived_mismatched": list(scan.result.derived_mismatched),
                    "derived_truncated": scan.result.derived_truncated,
                }
                for scan in self.scans
            ],
            "probes": [
                {
                    "command": probe.command,
                    "context": probe.context,
                    "outcome": probe.outcome,
                }
                for probe in self.probes
            ],
            "executed_commands": list(self.executed),
            "channel_reopens": self.reopens,
            "unreachable_contexts": list(self.unreachable),
            "probes_skipped": self.probes_skipped,
        }


def sample_command(
    command: str,
    samples: Sequence[tuple[str, str]],
    *,
    allow_generic: bool = True,
) -> str | None:
    """Make a command typeable, or None when a parameter cannot be filled in.

    `allow_generic` guards probing. A specific placeholder such as `IFNAME`
    names an object that already exists, so entering it changes nothing. A
    catch-all such as `WORD` appears on hundreds of unrelated commands, and
    filling it in invents a value: on a live switch `hostname WORD` renamed
    the device. Probes therefore refuse catch-alls while still entering
    interfaces, VLANs and other identified objects.
    """
    from .crawler import NUMERIC_RANGE_RE

    lookup = {token.casefold(): value for token, value in samples}
    tokens: list[str] = []
    for token in command.split():
        if option_kind(token) != "parameter":
            tokens.append(token)
            continue
        numeric = NUMERIC_RANGE_RE.match(token)
        if numeric:
            tokens.append(numeric.group("minimum"))
            continue
        if not allow_generic and token.strip("<>").upper() in GENERIC_PLACEHOLDERS:
            return None
        sample = lookup.get(token.casefold())
        if sample is None:
            return None
        tokens.append(sample)
    return " ".join(tokens)


def scan_modes(
    navigator: ModeNavigator,
    *,
    limits: CrawlLimits,
    device: dict[str, object] | None = None,
    hints: Sequence[str] = DEFAULT_MODE_HINTS,
    denylist: frozenset[str] = DEFAULT_PROBE_DENYLIST,
    workers: Sequence[ModeNavigator] = (),
    probe_generic_placeholders: bool = False,
    max_probes_per_context: int = 200,
    max_contexts: int = 64,
    root_probe_allowlist: frozenset[str] | None = None,
    on_context: object = None,
    on_progress: object = None,
    is_cancelled: object = None,
) -> ModeScanReport:
    """Walk the CLI as a graph of contexts, breadth first.

    Each context is crawled to completion with the ordinary help crawler, then
    its executable commands are probed to find the contexts they open. A probe
    that changes the prompt is an entry; anything else is recorded and left
    alone.
    """
    report = ModeScanReport()
    root_fingerprint = navigator.bind_root()
    root = ModeContext("root", root_fingerprint)
    queue: list[ModeContext] = [root]
    seen: set[tuple[str, str]] = {root.key}

    cancelled = is_cancelled if callable(is_cancelled) else None
    while queue and len(report.scans) < max_contexts:
        if cancelled and cancelled():
            break
        context = queue.pop(0)
        if not _ensure(navigator, context):
            report.unreachable.append(context.name)
            continue

        guard = GuardedHelp(navigator, context)
        # Help queries are read-only, so every worker that can be placed in the
        # same context shares the traversal. A worker that cannot get there is
        # left out rather than allowed to answer from somewhere else.
        helpers = [
            GuardedHelp(worker, context)
            for worker in workers
            if _ensure(worker, context)
        ]
        catalog = Catalog(device=dict(device or {}), mode="audit")
        result = crawl(
            guard,
            catalog,
            seeds=[],
            limits=limits,
            on_progress=on_progress if callable(on_progress) else None,
            is_cancelled=cancelled,
            extra_query_helps=helpers,
        )
        scan = ContextScan(context, catalog, result, guard.recoveries, guard.unverified)
        report.scans.append(scan)
        if callable(on_context):
            on_context(scan)

        candidates = _probe_candidates(
            scan, denylist, hints, limits.parameter_samples,
            root_probe_allowlist if context is root else None,
            probe_generic_placeholders,
        )
        # Probing executes what it types, and a large context offers thousands
        # of candidates of which only the first few plausibly open anything.
        # The order puts likely openers first; the rest are reported, not run.
        if len(candidates) > max_probes_per_context:
            report.probes_skipped += len(candidates) - max_probes_per_context
            candidates = candidates[:max_probes_per_context]

        progress = on_progress if callable(on_progress) else None
        for index, command in enumerate(candidates):
            if cancelled and cancelled():
                break
            if progress:
                # Probes run one command each and are the longest silent phase
                # after a crawl: hundreds of them at network round-trip speed.
                progress(
                    CrawlProgress(
                        queries=index,
                        max_queries=len(candidates),
                        prefix=command,
                        commands=0,
                        pending=len(candidates) - index,
                        stage="probe",
                    )
                )
            if not _ensure(navigator, context):
                report.unreachable.append(context.name)
                break
            record = _probe(navigator, context, command)
            report.probes.append(record)
            if record.outcome != "entered" or record.fingerprint is None:
                continue

            child = ModeContext(
                name=f"{context.name}/{command.split()[0]}",
                fingerprint=record.fingerprint,
                entry_path=context.entry_path + (command,),
                parent=context.fingerprint,
            )
            navigator.leave(context.fingerprint)
            if child.key in seen or len(seen) >= max_contexts:
                continue
            seen.add(child.key)
            queue.append(child)

    report.executed = list(navigator.executed)
    report.reopens = navigator.reopens
    return report


def _ensure(navigator: ModeNavigator, context: ModeContext) -> bool:
    """Place the session in `context`, repairing the channel if needed.

    A context that cannot be reached even from a fresh channel is genuinely
    gone (a mode that no longer exists, or an entry command that stopped
    working); anything short of that is repaired rather than reported.
    """
    try:
        navigator.ensure(context)
        return True
    except (NavigationError, OSError):
        pass
    try:
        navigator.recover()
        navigator.ensure(context)
        return True
    except (NavigationError, OSError):
        return False


def _probe_candidates(
    scan: ContextScan,
    denylist: frozenset[str],
    hints: Sequence[str],
    samples: Sequence[tuple[str, str]],
    allowlist: frozenset[str] | None,
    allow_generic: bool = False,
) -> list[str]:
    candidates: list[str] = []
    for command in scan.executables:
        if not is_probe_allowed(command, denylist):
            continue
        if allowlist is not None and command.split()[0].lower() not in allowlist:
            continue
        typed = sample_command(command, samples, allow_generic=allow_generic)
        if typed:
            candidates.append(typed)
    return probe_order(candidates, hints)


def _probe(navigator: ModeNavigator, context: ModeContext, command: str) -> ProbeRecord:
    output, fingerprint = navigator.run(command)
    if fingerprint is None:
        fingerprint = navigator.confirm_fingerprint()
    if fingerprint and fingerprint != context.fingerprint:
        return ProbeRecord(command, context.name, "entered", fingerprint)
    if any(ERROR_RE.match(line.strip()) for line in output.splitlines()):
        return ProbeRecord(command, context.name, "rejected")
    return ProbeRecord(command, context.name, "executed")
