from __future__ import annotations

import re
import threading
from bisect import bisect_left
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from .models import Catalog, CommandEntry
from .parser import (
    STRICT_PROFILE,
    HelpOption,
    ParserProfile,
    option_kind,
    parse_context_help,
)

NUMERIC_RANGE_RE = re.compile(r"^<(?P<minimum>-?\d+)(?:-|\.\.)(?P<maximum>-?\d+)>$")
# Negation branches mirror the whole tree and describe removal rather than
# capability, so they are walked last: a scan cut short by a limit or a dead
# session then loses the mirror, not the commands themselves.
LATE_TOKENS: frozenset[str] = frozenset({"no", "undo", "default"})
# Nested derivations resolve within a few passes; the bound stops a pathological
# grammar from looping.
MAX_REPLICATION_PASSES = 8


@dataclass(frozen=True)
class CrawlLimits:
    max_depth: int = 32
    max_queries: int = 100_000
    denied_tokens: frozenset[str] = frozenset()
    parameter_policy: str = "explore"
    parameter_samples: tuple[tuple[str, str], ...] = ()
    # A CLI grammar repeats itself: on the reference platform 18000 visited
    # nodes had only 694 distinct option sets, because enumerations (log
    # levels, interface kinds, logging sources) continue identically. Nodes
    # that repeat a known option set are copied instead of walked, and a
    # sample of the copies is verified against the device.
    deduplicate_subtrees: bool = True
    verify_samples: int = 25
    max_derived_entries: int = 500_000
    # How this platform's contextual help deviates from the strict reading.
    parser_profile: ParserProfile = STRICT_PROFILE


@dataclass(frozen=True)
class CrawlProgress:
    queries: int
    max_queries: int
    prefix: str
    commands: int
    pending: int = 0
    # Which stage of the scan is reporting: "crawl" (help queries), "verify"
    # (re-querying derived copies), "probe" (entering candidate modes), "save"
    # (serialising the catalog). A scan spends real minutes outside the crawl,
    # and a bar that goes silent there reads as a hang.
    stage: str = "crawl"


@dataclass(frozen=True)
class CrawlResult:
    queries: int
    complete: bool
    query_limit_reached: bool
    pending_nodes: int
    skipped_depth: int
    skipped_denied: int
    skipped_parameters: tuple[str, ...]
    cancelled: bool
    derived_nodes: int = 0
    derived_verified: int = 0
    derived_mismatched: tuple[str, ...] = ()
    derived_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "queries": self.queries,
            "query_limit_reached": self.query_limit_reached,
            "pending_nodes": self.pending_nodes,
            "skipped_depth": self.skipped_depth,
            "skipped_denied": self.skipped_denied,
            "skipped_parameters": list(self.skipped_parameters),
            "cancelled": self.cancelled,
            "derived_nodes": self.derived_nodes,
            "derived_verified": self.derived_verified,
            "derived_mismatched": list(self.derived_mismatched),
            "derived_truncated": self.derived_truncated,
        }


@dataclass(frozen=True)
class _CrawlNode:
    catalog_prefix: str
    query_prefix: str
    # Verification-only nodes confirm a documented command exists on the
    # device; their help output must not spawn new crawl branches, or
    # documentation alternatives explode combinatorially.
    expand: bool = True


def _parameter_sample(token: str, limits: CrawlLimits) -> str | None:
    samples = dict(limits.parameter_samples)
    if token in samples:
        return samples[token]
    casefolded = {key.casefold(): value for key, value in limits.parameter_samples}
    if token.casefold() in casefolded:
        return casefolded[token.casefold()]

    numeric_range = NUMERIC_RANGE_RE.match(token)
    if numeric_range:
        return numeric_range.group("minimum")
    return None


def _safe_cli_fragment(value: str) -> bool:
    return bool(value) and value.isascii() and value.isprintable() and "?" not in value


def _is_option_flag(token: str) -> bool:
    """Whether a token is an independent option flag such as "-q" or "--count".

    Flags combine freely with each other, so expanding them multiplies the
    queue: a utility with ten flags would generate millions of prefixes that
    describe no new commands. The flag itself is still catalogued.
    """
    return len(token) > 1 and token.startswith("-") and not token[1].isdigit()


def _seed_node(seed: str, limits: CrawlLimits) -> tuple[_CrawlNode | None, str | None]:
    normalized = " ".join(seed.split())
    if not normalized:
        return None, None

    query_tokens: list[str] = []
    for token in normalized.split():
        sample = _parameter_sample(token, limits)
        if option_kind(token) != "parameter" and sample is None:
            query_tokens.append(token)
            continue
        if limits.parameter_policy != "explore":
            return None, token
        if not sample or not _safe_cli_fragment(sample):
            return None, token
        query_tokens.append(sample)

    return (
        _CrawlNode(
            catalog_prefix=normalized + " ",
            query_prefix=" ".join(query_tokens) + " ",
        ),
        None,
    )


def _ingest_options(
    node: _CrawlNode,
    options: Sequence[HelpOption],
    catalog: Catalog,
    queue: deque[_CrawlNode],
    limits: CrawlLimits,
    skipped_parameters: set[str],
    late_queue: deque[_CrawlNode] | None = None,
) -> int:
    skipped_denied = 0
    parent_command = node.catalog_prefix.strip()
    parent = catalog.commands.get(parent_command) if parent_command else None
    if options and parent_command:
        parent = catalog.add(parent_command, "", "cli")
    if options and node.expand:
        # Every keyword below is catalogued before denial, option-flag and
        # parameter policy can act, so this node's keyword list is complete
        # even when the walk declines to descend through parts of it.
        catalog.enumerated.add(parent_command)

    for option in options:
        if option.kind == "cr":
            if parent:
                parent.executable = True
                if not parent.description:
                    parent.description = option.description
            continue

        if not node.expand:
            continue

        token = option.token
        if not _safe_cli_fragment(token):
            continue
        command = f"{node.catalog_prefix}{token}".strip()
        catalog.add(command, option.description, "cli")
        if parent and command not in parent.children:
            parent.children.append(command)

        if token.lower() in limits.denied_tokens:
            skipped_denied += 1
            continue

        if _is_option_flag(token):
            continue

        query_token = token
        if option.kind == "parameter":
            if limits.parameter_policy != "explore":
                skipped_parameters.add(token)
                continue
            sample = _parameter_sample(token, limits)
            if not sample or not _safe_cli_fragment(sample):
                skipped_parameters.add(token)
                continue
            query_token = sample

        child = _CrawlNode(
            catalog_prefix=command + " ",
            query_prefix=f"{node.query_prefix}{query_token} ",
        )
        # Only a top-level negation is deferred: `no` inside a branch is that
        # branch's own grammar, and holding it back would reorder one node.
        if late_queue is not None and not node.catalog_prefix and token.lower() in LATE_TOKENS:
            late_queue.append(child)
        else:
            queue.append(child)
    return skipped_denied


@dataclass(frozen=True)
class _DerivedNode:
    """A node whose subtree was copied from an identically shaped one."""

    catalog_prefix: str
    query_prefix: str
    canonical_prefix: str
    signature: frozenset[tuple[str, str]]


def _signature(options: Sequence[HelpOption]) -> frozenset[tuple[str, str]]:
    """What a node looks like: its option set, ignoring descriptions."""
    return frozenset((option.token, option.kind) for option in options)


def _expandable(options: Sequence[HelpOption]) -> bool:
    return any(option.kind != "cr" for option in options)


def _replicate(
    catalog: Catalog,
    derived: dict[str, _DerivedNode],
    max_depth: int,
    max_entries: int,
) -> tuple[int, bool]:
    """Copy each canonical subtree onto the node that repeats its shape.

    Copies obey `max_depth` like the walk does - a derived node sits deeper
    than its canonical, so copying blindly would reach past the bound the
    caller asked for. A canonical subtree can itself contain derived nodes,
    so this repeats until nothing new appears; each pass looks a subtree up by
    binary search instead of scanning the whole catalog per node, which is the
    difference between seconds and never finishing on a real command tree.
    """
    added = 0
    truncated = False
    for _ in range(MAX_REPLICATION_PASSES):
        commands = sorted(catalog.commands)
        pending: list[tuple[str, CommandEntry]] = []
        for node in derived.values():
            canonical = node.canonical_prefix + " "
            index = bisect_left(commands, canonical)
            while index < len(commands) and commands[index].startswith(canonical):
                command = commands[index]
                index += 1
                copy = node.catalog_prefix + command[len(node.canonical_prefix):]
                if copy in catalog.commands or len(copy.split()) > max_depth:
                    continue
                pending.append((copy, catalog.commands[command]))
            if added + len(pending) >= max_entries:
                truncated = True
                break
        for copy, source in pending:
            entry = catalog.add(copy, source.description, "cli")
            entry.executable = source.executable
        added += len(pending)
        if not pending or truncated:
            break
    return added, truncated


def _verify_derived(
    query_help: Callable[[str], str],
    derived: dict[str, _DerivedNode],
    limit: int,
    profile: ParserProfile,
    on_progress: Callable[[CrawlProgress], None] | None = None,
) -> tuple[int, list[str]]:
    """Ask the device whether derived nodes really look like their canonical.

    Deriving a subtree from an identical option set is an assumption, so a
    sample of it is checked against the device rather than trusted. Sampling
    is spread across the whole set instead of taking the first entries, which
    would all come from one branch.
    """
    candidates = list(derived.values())
    if not candidates:
        return 0, []
    step = max(1, len(candidates) // limit)
    sampled = candidates[::step][:limit]
    verified = 0
    mismatched: list[str] = []
    for node in sampled:
        if on_progress:
            on_progress(
                CrawlProgress(
                    queries=verified,
                    max_queries=len(sampled),
                    prefix=node.query_prefix,
                    commands=0,
                    pending=len(sampled) - verified,
                    stage="verify",
                )
            )
        observed = _signature(
            parse_context_help(
                query_help(node.query_prefix), node.query_prefix, profile
            )
        )
        verified += 1
        if observed != node.signature:
            mismatched.append(node.catalog_prefix)
    return verified, mismatched


def _load_seeds(
    seeds: Sequence[str],
    verify_seeds: Sequence[str],
    limits: CrawlLimits,
    deferred_seeds: deque[_CrawlNode],
    skipped_parameters: set[str],
) -> None:
    for expand, batch in ((True, seeds), (False, verify_seeds)):
        for seed in batch:
            node, skipped_parameter = _seed_node(seed, limits)
            if node:
                deferred_seeds.append(node if expand else replace(node, expand=False))
            if skipped_parameter:
                skipped_parameters.add(skipped_parameter)


def crawl(
    query_help: Callable[[str], str],
    catalog: Catalog,
    seeds: list[str],
    limits: CrawlLimits,
    *,
    include_root: bool = True,
    on_progress: Callable[[CrawlProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    extra_query_helps: Sequence[Callable[[str], str]] = (),
    verify_seeds: Sequence[str] = (),
) -> CrawlResult:
    if extra_query_helps:
        # Deduplication depends on the order nodes are visited in, which
        # parallel workers do not agree on; the parallel path stays exact.
        return _crawl_parallel(
            [query_help, *extra_query_helps],
            catalog,
            seeds,
            replace(limits, deduplicate_subtrees=False),
            include_root=include_root,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
            verify_seeds=verify_seeds,
        )

    queue: deque[_CrawlNode] = deque()
    if include_root:
        queue.append(_CrawlNode("", ""))
    deferred_seeds: deque[_CrawlNode] = deque()
    late: deque[_CrawlNode] = deque()
    skipped_parameters: set[str] = set()
    _load_seeds(seeds, verify_seeds, limits, deferred_seeds, skipped_parameters)
    if not include_root:
        queue.extend(deferred_seeds)
        deferred_seeds.clear()

    visited: set[tuple[str, str]] = set()
    canonical: dict[frozenset[tuple[str, str]], str] = {}
    derived: dict[str, _DerivedNode] = {}
    queries = 0
    skipped_depth = 0
    skipped_denied = 0
    cancelled = False

    while (queue or deferred_seeds or late) and queries < limits.max_queries:
        if not queue:
            # Ordinary branches first, then the seeds, and the negation mirror
            # last - the pass order a person would choose by hand.
            queue.extend(deferred_seeds)
            deferred_seeds.clear()
            if not queue:
                queue.extend(late)
                late.clear()
        if is_cancelled and is_cancelled():
            cancelled = True
            break
        node = queue.popleft()
        visit_key = (node.catalog_prefix, node.query_prefix)
        if visit_key in visited:
            continue
        if len(node.catalog_prefix.split()) >= limits.max_depth:
            skipped_depth += 1
            continue
        visited.add(visit_key)
        options = parse_context_help(
            query_help(node.query_prefix),
            node.query_prefix,
            limits.parser_profile,
        )
        queries += 1

        signature = _signature(options)
        if limits.deduplicate_subtrees and node.expand and _expandable(options):
            known = canonical.get(signature)
            if known is None:
                canonical[signature] = node.catalog_prefix.strip()
            elif known != node.catalog_prefix.strip():
                # This node looks exactly like one already walked; its subtree
                # is copied after the crawl instead of being queried again.
                derived[node.catalog_prefix.strip()] = _DerivedNode(
                    catalog_prefix=node.catalog_prefix.strip(),
                    query_prefix=node.query_prefix,
                    canonical_prefix=known,
                    signature=signature,
                )
                # The node itself is still recorded (including whether it can
                # be executed); only its branches are copied rather than walked.
                _ingest_options(
                    replace(node, expand=False),
                    options,
                    catalog,
                    queue,
                    limits,
                    skipped_parameters,
                    late_queue=late,
                )
                continue

        skipped_denied += _ingest_options(
            node, options, catalog, queue, limits, skipped_parameters, late_queue=late
        )
        if on_progress:
            on_progress(
                CrawlProgress(
                    queries=queries,
                    max_queries=limits.max_queries,
                    prefix=node.catalog_prefix,
                    commands=len(catalog.commands),
                    pending=len(queue) + len(deferred_seeds) + len(late),
                )
            )
    _, derived_truncated = _replicate(
        catalog, derived, limits.max_depth, limits.max_derived_entries
    )
    derived_verified, derived_mismatched = (
        _verify_derived(
            query_help,
            derived,
            limits.verify_samples,
            limits.parser_profile,
            on_progress,
        )
        if derived and limits.verify_samples
        else (0, [])
    )
    queries += derived_verified

    pending_nodes = len(queue) + len(deferred_seeds) + len(late)
    query_limit_reached = queries >= limits.max_queries and pending_nodes > 0
    complete = not (
        query_limit_reached
        or skipped_depth
        or skipped_denied
        or skipped_parameters
        or cancelled
        or derived_mismatched
        or derived_truncated
    )
    return CrawlResult(
        derived_nodes=len(derived),
        derived_truncated=derived_truncated,
        derived_verified=derived_verified,
        derived_mismatched=tuple(derived_mismatched),
        queries=queries,
        complete=complete,
        query_limit_reached=query_limit_reached,
        pending_nodes=pending_nodes,
        skipped_depth=skipped_depth,
        skipped_denied=skipped_denied,
        skipped_parameters=tuple(sorted(skipped_parameters)),
        cancelled=cancelled,
    )


def _crawl_parallel(
    helpers: list[Callable[[str], str]],
    catalog: Catalog,
    seeds: list[str],
    limits: CrawlLimits,
    *,
    include_root: bool,
    on_progress: Callable[[CrawlProgress], None] | None,
    is_cancelled: Callable[[], bool] | None,
    verify_seeds: Sequence[str] = (),
) -> CrawlResult:
    queue: deque[_CrawlNode] = deque()
    if include_root:
        queue.append(_CrawlNode("", ""))
    deferred_seeds: deque[_CrawlNode] = deque()
    late: deque[_CrawlNode] = deque()
    skipped_parameters: set[str] = set()
    _load_seeds(seeds, verify_seeds, limits, deferred_seeds, skipped_parameters)
    if not include_root:
        queue.extend(deferred_seeds)
        deferred_seeds.clear()

    visited: set[tuple[str, str]] = set()
    condition = threading.Condition()
    state = {
        "queries": 0,
        "in_flight": 0,
        "skipped_depth": 0,
        "skipped_denied": 0,
        "cancelled": False,
    }
    errors: list[BaseException] = []

    def next_node() -> _CrawlNode | None:
        # Deferred seeds start only once the main tree is fully drained, and
        # the negation mirror only after those, matching the sequential order.
        while queue or deferred_seeds or late:
            if not queue:
                if state["in_flight"]:
                    return None
                if deferred_seeds:
                    queue.extend(deferred_seeds)
                    deferred_seeds.clear()
                else:
                    queue.extend(late)
                    late.clear()
                continue
            node = queue.popleft()
            visit_key = (node.catalog_prefix, node.query_prefix)
            if visit_key in visited:
                continue
            if len(node.catalog_prefix.split()) >= limits.max_depth:
                state["skipped_depth"] += 1
                continue
            visited.add(visit_key)
            return node
        return None

    def worker(query_help: Callable[[str], str]) -> None:
        while True:
            with condition:
                node = None
                while node is None:
                    if errors or state["cancelled"]:
                        return
                    if is_cancelled and is_cancelled():
                        state["cancelled"] = True
                        condition.notify_all()
                        return
                    if state["queries"] + state["in_flight"] >= limits.max_queries:
                        if not state["in_flight"]:
                            return
                        condition.wait(0.1)
                        continue
                    node = next_node()
                    if node is None:
                        if not state["in_flight"]:
                            return
                        condition.wait(0.1)
                state["in_flight"] += 1
            try:
                options = parse_context_help(
                    query_help(node.query_prefix),
                    node.query_prefix,
                    limits.parser_profile,
                )
            except Exception as error:  # noqa: BLE001 - re-raised in the main thread
                with condition:
                    errors.append(error)
                    state["in_flight"] -= 1
                    condition.notify_all()
                return
            with condition:
                state["queries"] += 1
                state["in_flight"] -= 1
                state["skipped_denied"] += _ingest_options(
                    node, options, catalog, queue, limits, skipped_parameters,
                    late_queue=late,
                )
                if on_progress:
                    on_progress(
                        CrawlProgress(
                            queries=state["queries"],
                            max_queries=limits.max_queries,
                            prefix=node.catalog_prefix,
                            commands=len(catalog.commands),
                            pending=len(queue) + len(deferred_seeds) + len(late),
                        )
                    )
                condition.notify_all()

    threads = [
        threading.Thread(target=worker, args=(helper,), daemon=True)
        for helper in helpers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]

    pending_nodes = len(queue) + len(deferred_seeds) + len(late)
    query_limit_reached = state["queries"] >= limits.max_queries and pending_nodes > 0
    complete = not (
        query_limit_reached
        or state["skipped_depth"]
        or state["skipped_denied"]
        or skipped_parameters
        or state["cancelled"]
    )
    return CrawlResult(
        queries=state["queries"],
        complete=complete,
        query_limit_reached=query_limit_reached,
        pending_nodes=pending_nodes,
        skipped_depth=state["skipped_depth"],
        skipped_denied=state["skipped_denied"],
        skipped_parameters=tuple(sorted(skipped_parameters)),
        cancelled=state["cancelled"],
    )
