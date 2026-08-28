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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from .crawler import CrawlLimits, CrawlProgress, CrawlResult, crawl
from .models import Catalog
from .navigator import (
    DEFAULT_MODE_ENTRY_VERBS,
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


# The skim pass, in two numbers. A context's "top" is one help query: the depth
# check skips any node already carrying a word, so a depth of 1 asks the context
# itself and nothing below it. The second pass reopens exactly the verbs that
# could be doors, one query each, because that is the single thing the top
# cannot say - whether `vlan` is a statement or a mode.
SKIM_TOP_DEPTH = 1
SKIM_ENTRY_DEPTH = 2


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
    # "entered"   - the prompt changed, a new context was found
    # "executed"  - the command ran and the prompt did not change: on a live
    #               device this is a configuration change, not a discovery
    # "rejected"  - the device answered with an error and changed nothing
    # "reset"     - the command took the session down (no prompt answered
    #               afterwards); attributed rather than mislabelled "executed"
    outcome: str
    fingerprint: str | None = None


@dataclass
class ModeScanReport:
    scans: list[ContextScan] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    reopens: int = 0
    unreachable: list[str] = field(default_factory=list)
    # The probe that took the session down, if one did. Named so an operator
    # can see what to avoid and, under the safe policy, that it should not have
    # been typed at all.
    reset_by: str | None = None
    probes_skipped: int = 0
    # Candidates that were never typed because a parameter had no configured
    # sample. Reported rather than filled in with an invented value: this is
    # the number that tells an operator what `discovery.parameter_samples`
    # would buy, and the placeholders say which samples to add.
    probes_unsampled: int = 0
    unsampled_parameters: set[str] = field(default_factory=set)

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
            "reset_by": self.reset_by,
            "probes_skipped": self.probes_skipped,
            "probes_unsampled": self.probes_unsampled,
            "unsampled_parameters": sorted(self.unsampled_parameters),
        }


def sample_command(
    command: str,
    samples: Sequence[tuple[str, str]],
    *,
    allow_generic: bool = True,
) -> str | None:
    """Make a command typeable, or None when a parameter cannot be filled in.

    `allow_generic` separates reading from writing. A help query is harmless
    whatever value it carries, so the crawler fills a numeric range with its
    minimum and a catch-all with a configured sample.

    A probe executes what it types, and there an invented value is a change
    nobody asked for. The minimum of a range reads as innocent and is not:
    inside an interface context `speed <10-40000>` becomes `speed 10`, `mtu
    <68-9216>` becomes `mtu 68` and `spanning-tree priority <0-61440>` becomes
    priority 0 - each one applied to a live port. A catch-all is no better:
    `hostname WORD` renamed a switch in a lab. So a probe types only values the
    operator supplied in `discovery.parameter_samples`; anything else is left
    unprobed and counted, which costs contexts rather than the device.
    """
    from .crawler import NUMERIC_RANGE_RE

    lookup = {token.casefold(): value for token, value in samples}
    tokens: list[str] = []
    for token in command.split():
        if option_kind(token) != "parameter":
            tokens.append(token)
            continue
        generic = token.strip("<>").upper() in GENERIC_PLACEHOLDERS
        sample = lookup.get(token.casefold())
        if sample is not None and (allow_generic or not generic):
            tokens.append(sample)
            continue
        if not allow_generic:
            return None
        numeric = NUMERIC_RANGE_RE.match(token)
        if numeric:
            tokens.append(numeric.group("minimum"))
            continue
        return None
    return " ".join(tokens)


def scan_modes(
    navigator: ModeNavigator,
    *,
    limits: CrawlLimits,
    device: dict[str, object] | None = None,
    hints: Sequence[str] = DEFAULT_MODE_HINTS,
    denylist: frozenset[str] = DEFAULT_PROBE_DENYLIST,
    workers: Sequence[ModeNavigator] = (),
    # Off by default: a probe executes what it types, so inventing a value is
    # a change nobody asked for. Turn it on for a lab device, where entering
    # every context matters more than what entering it costs.
    probe_invented_values: bool = False,
    max_probes_per_context: int = 200,
    max_contexts: int = 64,
    root_probe_allowlist: frozenset[str] | None = None,
    # The safe default: probe only commands whose head verb opens a container
    # the session can step back out of, in every context, not just at the root.
    # Set to None for the aggressive policy that probes every executable leaf -
    # reaches more modes but executes the statements that are not modes.
    mode_entry_verbs: frozenset[str] | None = DEFAULT_MODE_ENTRY_VERBS,
    on_context: object = None,
    on_progress: object = None,
    is_cancelled: object = None,
    start_contexts: Sequence[ModeContext] | None = None,
    descend: bool = True,
    harvested_entries: Mapping[str, Sequence[str]] | None = None,
    skim: bool = False,
    focus_verbs: frozenset[str] | None = None,
) -> ModeScanReport:
    """Walk the CLI as a graph of contexts, breadth first.

    Each context is crawled to completion with the ordinary help crawler, then
    its executable commands are probed to find the contexts they open. A probe
    that changes the prompt is an entry; anything else is recorded and left
    alone.

    ``start_contexts`` scopes the walk: given one or more contexts (rebuilt from
    a prior scan's map), the breadth-first walk begins there instead of at the
    root, so only those contexts and the subtree they open are scanned. The root
    is still bound so entry paths can be replayed and contexts stepped back out
    of; it is simply not itself enqueued.

    ``descend`` False scans only the contexts it is given and does not follow the
    modes they open - "this block, not the tree below it", the shallow run the
    exec-only choice needs. The children are still recorded as probes, so the
    map keeps learning the shape even when this run declines to walk into it.

    ``harvested_entries`` maps a head verb to real instance lines lifted from
    the device's own running configuration ("interface" -> ["interface
    10ge1/0/10"]). They fill the gap the no-invented-values policy leaves: a
    context entered by instance can be probed with a value that already exists
    on the device, so typing it changes nothing. Each line is only tried in a
    context whose crawled surface offers that verb, and the denylist and
    mode-entry allowlist still apply.

    ``focus_verbs`` narrows the probes typed in the contexts the run starts
    from to those head verbs, which is how the map browser turns "open the VLAN
    block" into a run that types vlan commands and nothing else. Contexts found
    below a start are probed normally - the point is to reach one block quickly,
    then map it properly. Without it every candidate the policy allows is tried.

    ``skim`` trades depth for speed: every context is topped rather than walked
    (see ``_skim_context``), which is what turns the first pass over a device
    from a long catalogue into a map drawn in seconds. The contexts found are
    the same ones; only what is known inside each is thinner, and each says so
    by reporting itself incomplete.
    """
    report = ModeScanReport()
    root_fingerprint = navigator.bind_root()
    root = ModeContext("root", root_fingerprint)
    if start_contexts:
        queue: list[ModeContext] = list(start_contexts)
        seen: set[tuple[str, str]] = {context.key for context in start_contexts}
    else:
        queue = [root]
        seen = {root.key}
    # Where a focus applies: the contexts this run was pointed at, not the ones
    # it discovers inside them.
    focus_keys = set(seen) if focus_verbs else set()

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
        # The safe policy narrows every context to mode-entry verbs; without it
        # the old behaviour stands, constraining the root alone. The skim needs
        # this before it crawls - the allowlist is what it expands - so it is
        # settled here rather than after the walk.
        allowlist = (
            mode_entry_verbs
            if mode_entry_verbs is not None
            else (root_probe_allowlist if context is root else None)
        )
        catalog = Catalog(device=dict(device or {}), mode="audit")
        result = (
            _skim_context(
                guard, catalog, limits, allowlist, helpers, on_progress, cancelled
            )
            if skim
            else crawl(
                guard,
                catalog,
                seeds=[],
                limits=limits,
                on_progress=on_progress if callable(on_progress) else None,
                is_cancelled=cancelled,
                extra_query_helps=helpers,
            )
        )
        scan = ContextScan(context, catalog, result, guard.recoveries, guard.unverified)
        report.scans.append(scan)
        if callable(on_context):
            on_context(scan)

        candidates, unsampled = _probe_candidates(
            scan, denylist, hints, limits.parameter_samples,
            allowlist,
            probe_invented_values,
        )
        if harvested_entries:
            candidates, unsampled = _add_harvested(
                candidates, unsampled, harvested_entries, denylist, allowlist,
                surface=scan.catalog.commands,
            )
        if focus_verbs and context.key in focus_keys:
            candidates = [
                command for command in candidates
                if command.split()[0].lower() in focus_verbs
            ]
        report.probes_unsampled += len(unsampled)
        report.unsampled_parameters.update(
            token for command in unsampled for token in command.split()
            if option_kind(token) == "parameter"
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
            if record.outcome == "reset":
                # The probe took the session down. Typing the remaining
                # candidates into a dead channel is what left a tail of useless
                # `exit`s on real hardware, so rebuild once and, if the device
                # is genuinely gone, stop the whole scan and remember the
                # command that did it rather than retrying it.
                report.reset_by = command
                if not _ensure(navigator, context):
                    report.unreachable.append(context.name)
                    queue.clear()
                    break
                continue
            if record.outcome != "entered" or record.fingerprint is None:
                continue

            child = ModeContext(
                name=f"{context.name}/{command.split()[0]}",
                fingerprint=record.fingerprint,
                entry_path=context.entry_path + (command,),
                parent=context.fingerprint,
            )
            navigator.leave(context.fingerprint)
            # A shallow scoped run records what each probe opens but does not
            # enqueue it: the operator asked for this block alone, not the modes
            # below it. The entry is still in report.probes, so the map still
            # learns the child exists.
            if not descend:
                continue
            if child.key in seen or len(seen) >= max_contexts:
                continue
            seen.add(child.key)
            queue.append(child)

    report.executed = list(navigator.executed)
    report.reopens = navigator.reopens
    return report


def _skim_context(
    guard: GuardedHelp,
    catalog: Catalog,
    limits: CrawlLimits,
    allowlist: frozenset[str] | None,
    helpers: Sequence[GuardedHelp],
    on_progress: object,
    cancelled: object,
) -> CrawlResult:
    """Take the top off a context instead of walking it out.

    Two passes. The first asks a single help query and records the context's own
    top-level verbs - the "top" an operator reads off the screen. The second
    reopens only the verbs that could lead somewhere, because that is the one
    thing the top cannot say: whether a verb is a statement or a door. What
    hangs under a door is left for the deep parse of that block, on demand.

    The saving is the whole point: a context offering eighty verbs costs one
    query plus the handful that are mode entries, instead of eighty-one. The
    result is reported incomplete by construction - a skim knows it skipped the
    rest, and this project would rather under-claim than let a partial map read
    as the whole device.
    """
    progress = on_progress if callable(on_progress) else None
    workers = list(helpers)
    top = crawl(
        guard,
        catalog,
        seeds=[],
        limits=replace(limits, max_depth=min(SKIM_TOP_DEPTH, limits.max_depth)),
        on_progress=progress,
        is_cancelled=cancelled,
        extra_query_helps=workers,
    )
    verbs = sorted(
        {
            command.split()[0]
            for command in catalog.commands
            if command.split()
            and (allowlist is None or command.split()[0].lower() in allowlist)
        }
    )
    if not verbs or (cancelled and cancelled()):
        return replace(top, complete=False)
    entries = crawl(
        guard,
        catalog,
        seeds=verbs,
        limits=replace(limits, max_depth=min(SKIM_ENTRY_DEPTH, limits.max_depth)),
        include_root=False,  # the top is already recorded; expand the doors only
        on_progress=progress,
        is_cancelled=cancelled,
        extra_query_helps=workers,
    )
    return CrawlResult(
        queries=top.queries + entries.queries,
        complete=False,
        query_limit_reached=top.query_limit_reached or entries.query_limit_reached,
        pending_nodes=top.pending_nodes + entries.pending_nodes,
        skipped_depth=top.skipped_depth + entries.skipped_depth,
        skipped_denied=top.skipped_denied + entries.skipped_denied,
        skipped_parameters=tuple(
            sorted({*top.skipped_parameters, *entries.skipped_parameters})
        ),
        cancelled=top.cancelled or entries.cancelled,
        derived_nodes=entries.derived_nodes,
        derived_verified=entries.derived_verified,
        derived_mismatched=entries.derived_mismatched,
        derived_truncated=entries.derived_truncated,
    )


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
) -> tuple[list[str], list[str]]:
    """Probe candidates, and the commands left untried for want of a sample."""
    candidates: list[str] = []
    unsampled: list[str] = []
    for command in scan.executables:
        if not is_probe_allowed(command, denylist):
            continue
        if allowlist is not None and command.split()[0].lower() not in allowlist:
            continue
        typed = sample_command(command, samples, allow_generic=allow_generic)
        if typed:
            candidates.append(typed)
        else:
            unsampled.append(command)
    return probe_order(candidates, hints), unsampled


def _add_harvested(
    candidates: list[str],
    unsampled: list[str],
    harvested: Mapping[str, Sequence[str]],
    denylist: frozenset[str],
    allowlist: frozenset[str] | None,
    surface: Mapping[str, object] | None = None,
) -> tuple[list[str], list[str]]:
    """Substitute real running-config lines for commands the context offers.

    A parameterised command may be left unsampled - or fail to prove itself
    executable at all, when its generic placeholder cannot be filled without
    inventing a value. If the running configuration holds real instances under
    a head verb the context's crawled surface offers, those lines are typeable
    at zero risk - the device already carries them - so they join the
    candidates FIRST (a certain entry beats a guessed one) and any matching
    unsampled command stops counting as left untried. The denylist and
    mode-entry allowlist gate them exactly like any candidate.
    """
    heads_wanting = {command.split()[0].lower() for command in unsampled}
    # The crawled surface names every verb this context offers, including the
    # ones whose parameter kept them out of `executables` entirely.
    for command in surface or ():
        heads_wanting.add(command.split()[0].lower())
    added: list[str] = []
    covered_heads: set[str] = set()
    existing = set(candidates)
    for head in sorted(heads_wanting):
        if allowlist is not None and head not in allowlist:
            continue
        for line in harvested.get(head, ()):
            if line in existing or not is_probe_allowed(line, denylist):
                continue
            added.append(line)
            existing.add(line)
            covered_heads.add(head)
    if not added:
        return candidates, unsampled
    remaining = [
        command
        for command in unsampled
        if command.split()[0].lower() not in covered_heads
    ]
    return added + candidates, remaining


def _probe(navigator: ModeNavigator, context: ModeContext, command: str) -> ProbeRecord:
    output, fingerprint = navigator.run(command)
    if fingerprint is None:
        fingerprint = navigator.confirm_fingerprint()
    if fingerprint is None:
        # The command was sent and nothing answers the prompt afterwards: the
        # probe took the session down rather than doing nothing. Recording this
        # as "executed" would hide the cause of a dead scan among the harmless
        # ones; naming it lets the caller recover and quarantine the command.
        return ProbeRecord(command, context.name, "reset")
    if fingerprint != context.fingerprint:
        return ProbeRecord(command, context.name, "entered", fingerprint)
    if any(ERROR_RE.match(line.strip()) for line in output.splitlines()):
        return ProbeRecord(command, context.name, "rejected")
    return ProbeRecord(command, context.name, "executed")
