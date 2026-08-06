from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import Path

import yaml

from . import __version__
from .config import AppConfig, load_config
from .crawler import CrawlLimits, CrawlProgress, crawl
from .docs import scan_documentation
from .exceptions import CLIRadarError, ConfigurationError, DeviceConnectionError
from .export import render_human_yaml, render_tree_yaml
from .models import Catalog
from .report import render_html_report


class ExitCode(IntEnum):
    OK = 0
    USAGE = 2
    CONNECTION = 3
    SCAN = 4


def _write_private_text(destination: Path, content: str, artifact: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise RuntimeError(f"Refusing to write {artifact} through a symbolic link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        else:
            os.chmod(destination, stat.S_IREAD | stat.S_IWRITE)
    except Exception:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_catalog(catalog: Catalog, destination: Path) -> None:
    content = yaml.safe_dump(catalog.to_dict(), sort_keys=False, allow_unicode=True)
    _write_private_text(destination, content, "a catalog")


def write_html_report(catalog: Catalog, destination: Path) -> None:
    _write_private_text(destination, render_html_report(catalog), "an HTML report")


def write_exports(catalog: Catalog, config: AppConfig) -> None:
    _write_private_text(config.output.tree_catalog, render_tree_yaml(catalog), "a command tree")
    _write_private_text(
        config.output.human_catalog, render_human_yaml(catalog), "a command summary"
    )


_MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b|\b(?:[0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}\b")
_LABELLED_IDENTITY_RE = re.compile(
    r"^(?P<label>[^:]*\b(?:serial|s/n|sn|system\s+id|chassis\s+id|host\s*name|"
    r"hostname|device\s+name|mac\s+address|base\s+ethernet)\b[^:]*:)\s*\S.*$",
    re.IGNORECASE | re.MULTILINE,
)
_PROMPT_TAIL_RE = re.compile(r"^\S*[>#\$]\s*.*$")
# A configuration dump is a legitimate thing to stamp a report with, but it
# carries credentials. Whole lines go rather than the value alone: the secret
# is often the last of several tokens, and a partial match would leak it.
_SECRET_LINE_RE = re.compile(
    r"^\s*.*\b(?:password|secret|community|pre-shared|psk|"
    r"snmp-server\s+(?:user|community)|key(?:-string)?|md5|hmac|"
    r"authentication-key|shared-key|private-key|certificate)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def redact_identity(output: str, command: str) -> str:
    """Keep the firmware facts, drop what identifies the box.

    The catalog promises `identity: redacted`, and a version banner is exactly
    where a hostname, serial number and base MAC live. An operator may also
    point this at a configuration dump, which carries credentials outright.
    Removing both here keeps that promise true for the only commands CLIRadar
    executes for its own sake.
    """
    lines = output.splitlines()

    def _is_echo(line: str) -> bool:
        """The echo is the command alone, or a prompt with the command after it.

        Matching a bare `endswith` would eat a real banner line that happens to
        end in the same words.
        """
        stripped = line.strip()
        if stripped == command:
            return True
        if not command or not stripped.endswith(command):
            return False
        return stripped[: -len(command)].rstrip().endswith(("#", ">", "$"))

    # The device echoes the command back and redraws its prompt afterwards;
    # both carry the hostname even when the banner itself does not.
    while lines and (not lines[0].strip() or _is_echo(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _PROMPT_TAIL_RE.match(lines[-1].strip())):
        lines.pop()
    text = "\n".join(lines)
    text = _SECRET_LINE_RE.sub("<redacted line>", text)
    text = _LABELLED_IDENTITY_RE.sub(lambda m: f"{m.group('label')} <redacted>", text)
    return _MAC_RE.sub("<redacted>", text).strip()


def capture_firmware(session: object, commands: tuple[str, ...]) -> dict[str, object]:
    """Record which firmware the scan describes.

    A command catalog is only meaningful next to the software that produced
    it, so this runs before the crawl and its failure is never fatal: an
    unstamped report is worth more than no report. Vendors disagree on the
    verb, so every configured command is tried and each result kept.
    """
    captured: list[dict[str, str]] = []
    for command in commands:
        try:
            output = session.run_command(command)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - a stamp must never end a scan
            captured.append({"command": command, "error": str(error)})
            continue
        captured.append({"command": command, "output": redact_identity(output, command)})
    return {"captured_at_start": True, "results": captured}


def capture_running_config(session: object, commands: tuple[str, ...]) -> tuple[str, str]:
    """Read the running configuration, trying each dialect in turn.

    Vendors disagree on the verb and a wrong guess is answered with an error
    rather than a configuration, so the first answer that looks like one wins.
    Failure is never fatal: a command surface without the configuration beside
    it is still the thing the operator asked for.
    """
    from .parser import ERROR_RE, clean_terminal_output

    for command in commands:
        try:
            output = session.capture_output(command)  # type: ignore[attr-defined]
        # A refused dialect is not a failure; the next one is tried.
        except Exception:  # noqa: BLE001, S112  # nosec B112
            continue
        body = clean_terminal_output(output)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        # A rejected command answers in one or two lines, all of them errors.
        meaningful = [line for line in lines if not ERROR_RE.match(line)]
        if len(meaningful) > 3:
            return command, output
    return "", ""


def merge_context_scan(catalog: Catalog, scan: object) -> None:
    """Fold one context's commands into the shared catalog.

    Commands are keyed by their full path from the root, so a command that
    only exists inside a mode reads as the sequence a person would type:
    "configure vlan 1 name".
    """
    context = scan.context  # type: ignore[attr-defined]
    prefix = " ".join(context.entry_path)
    for command, entry in scan.catalog.commands.items():  # type: ignore[attr-defined]
        merged = catalog.add(f"{prefix} {command}".strip(), entry.description, "cli")
        merged.executable = merged.executable or entry.executable
    for node in scan.catalog.enumerated:  # type: ignore[attr-defined]
        catalog.enumerated.add(f"{prefix} {node}".strip())


@dataclass(frozen=True)
class ScanOutcome:
    """What one run produced, for the caller that has to report it."""

    catalog_path: Path
    html_path: Path | None
    commands: int
    queries: int
    complete: bool
    config_path: Path | None = None
    config_summary: dict[str, object] | None = None
    # Documented commands left unverified against the device because the compare
    # budget was reached; reported so "missing" is not confused with "unchecked".
    verify_skipped: int = 0


def _compare_verify_seeds(
    documented: dict[str, object], limit: int
) -> tuple[list[str], int]:
    """The documented commands to re-check on the device, and how many were cut.

    Verifying every command in a full vendor manual is thousands of round-trips
    that can run for many minutes, so the pass is bounded. Sorting keeps the run
    deterministic; a limit of 0 means verify everything.
    """
    ordered = sorted(documented)
    if limit and len(ordered) > limit:
        return ordered[:limit], len(ordered) - limit
    return ordered, 0


def _config_unexplained(catalog: Catalog) -> int:
    """Configured lines the crawled catalog could not explain.

    The device's own running configuration is evidence the crawl does not
    control: a line the box is actually running, that its `?` help never
    produced, proves the command surface is incomplete regardless of what the
    help claimed. This is what keeps completeness independent of trusting the
    firmware - a buggy `?` cannot report a surface as whole while the device is
    configured with commands it never mentioned.
    """
    configuration = catalog.configuration or {}
    try:
        return int(configuration.get("unmatched", 0))
    except (TypeError, ValueError):
        return 0


def _view_prefixes(mode_report: object) -> tuple[tuple[str, ...], ...]:
    """The entry paths of every context the scan actually stood in.

    A configured line lives inside a view, and the catalog stores each command
    under the path the scan walked to reach it. Matching the two therefore
    needs the list of those paths - and it has to come from the scan, because
    inventing it would mean naming this platform's modes in code.
    """
    if mode_report is None:
        return ()
    paths: list[tuple[str, ...]] = []
    for scan in getattr(mode_report, "scans", []):
        entry_path = tuple(scan.context.entry_path)
        if entry_path and entry_path not in paths:
            paths.append(entry_path)
    # Shortest first: a line should match under the outermost view that
    # explains it rather than an accidental deeper namesake.
    return tuple(sorted(paths, key=len))


def apply_config_scan(
    catalog: Catalog,
    config: AppConfig,
    command: str,
    output: str,
    view_prefixes: tuple[tuple[str, ...], ...] = (),
) -> None:
    """Match the running configuration against the catalog and save both."""
    from .runconfig import correlate, parse_config, redact_secrets, render_config_yaml

    lines = parse_config(redact_secrets(output), command)
    coverage = correlate(lines, catalog, command=command, view_prefixes=view_prefixes)
    catalog.configuration = coverage.to_dict()
    _write_private_text(
        config.output.config_tree,
        render_config_yaml(lines, coverage),
        "a configuration tree",
    )


def build_catalog(
    config: AppConfig,
    mode: str,
    docs_path: Path,
    on_progress: Callable[[CrawlProgress], None] | None = None,
    on_context: Callable[[object], None] | None = None,
) -> ScanOutcome:
    if mode not in {"compare", "audit", "docs"}:
        raise ConfigurationError("mode must be 'compare', 'audit', or 'docs'")
    if mode in {"compare", "docs"} and not docs_path.exists():
        raise ConfigurationError(f"documentation path not found: {docs_path}")

    # Never persist target or workstation identifiers in generated artifacts.
    catalog = Catalog(device={"identity": "redacted"}, mode=mode)
    documented = scan_documentation(docs_path) if mode in {"compare", "docs"} else {}
    catalog.commands.update(documented)
    destination = config.output.catalog_for(mode)

    if mode == "docs":
        catalog.scan = {
            "complete": True,
            "source": "documentation",
            "queries": 0,
        }
        write_catalog(catalog, destination)
        write_exports(catalog, config)
        return ScanOutcome(destination, None, len(catalog.commands), 0, True)

    import paramiko

    from .session import SwitchSession
    from .telnet import TelnetSession

    session_factory = TelnetSession if config.device.transport == "telnet" else SwitchSession

    from .parser import ParserProfile

    limits = CrawlLimits(
        parser_profile=ParserProfile(
            accept_undescribed_options=config.discovery.accept_undescribed_options,
            error_words_are_commands=config.discovery.error_words_are_commands,
        ),
        max_depth=config.discovery.max_depth,
        max_queries=config.discovery.max_queries,
        denied_tokens=frozenset(config.discovery.denied_tokens),
        parameter_policy=config.discovery.parameter_policy,
        parameter_samples=config.discovery.parameter_samples,
        deduplicate_subtrees=config.discovery.deduplicate_subtrees,
        verify_samples=config.discovery.verify_samples,
    )
    seeds = list(config.discovery.seed_commands)
    verify_seeds, verify_skipped = (
        _compare_verify_seeds(documented, config.discovery.compare_verify_limit)
        if mode == "compare"
        else ([], 0)
    )
    # One wall-clock ceiling for the whole run, checked between queries through
    # the crawler's existing cancellation hook - no separate machinery. When it
    # trips the scan unwinds cleanly and reports itself incomplete, so a run can
    # neither hang nor keep hammering the device past the budget.
    run_deadline = (
        time.monotonic() + config.discovery.max_runtime
        if config.discovery.max_runtime
        else None
    )

    def budget_reached() -> bool:
        return run_deadline is not None and time.monotonic() >= run_deadline

    mode_report = None
    config_command, config_output = "", ""
    try:
        with session_factory(config.device.to_session_dict(), config.output.raw_log) as session:
            if config.discovery.version_commands:
                catalog.device["firmware"] = capture_firmware(
                    session, config.discovery.version_commands
                )
            if config.discovery.config_commands:
                # Read before the crawl: it is one read-only command, and a
                # session that dies later still leaves the configuration behind
                # to be matched against whatever the crawl managed to collect.
                config_command, config_output = capture_running_config(
                    session, config.discovery.config_commands
                )
            if config.discovery.enter_modes:
                from .modes import scan_modes
                from .navigator import DEFAULT_MODE_ENTRY_VERBS, ModeNavigator

                # The safe policy restricts probes to reversible mode entries;
                # the aggressive policy (a lab opt-in) lifts that restriction.
                mode_entry_verbs = (
                    None
                    if config.discovery.probe_policy == "aggressive"
                    else (
                        frozenset(config.discovery.mode_entry_verbs)
                        or DEFAULT_MODE_ENTRY_VERBS
                    )
                )

                workers = [
                    ModeNavigator(terminal=sibling)
                    for sibling in session.open_extra_sessions(
                        config.discovery.parallel_channels - 1
                    )
                ]
                for worker in workers:
                    worker.bind_root()

                def persist(context_scan: object) -> None:
                    """Keep the catalog on disk as contexts complete.

                    A graph walk can take a long time, and a session that dies
                    halfway should cost the remaining contexts, not the ones
                    already collected.
                    """
                    merge_context_scan(catalog, context_scan)
                    catalog.scan = {"complete": False, "source": "context-graph", "partial": True}
                    if on_progress:
                        # Serialising a large catalog takes long enough to look
                        # like a hang if nothing says it is happening.
                        on_progress(
                            CrawlProgress(
                                queries=0,
                                max_queries=0,
                                prefix="",
                                commands=len(catalog.commands),
                                pending=0,
                                stage="save",
                            )
                        )
                    write_catalog(catalog, destination)
                    if on_context:
                        on_context(context_scan)

                mode_report = scan_modes(
                    ModeNavigator(terminal=session),
                    limits=limits,
                    device=catalog.device,
                    max_contexts=config.discovery.max_contexts,
                    max_probes_per_context=config.discovery.max_probes_per_context,
                    probe_invented_values=config.discovery.probe_invented_values,
                    mode_entry_verbs=mode_entry_verbs,
                    workers=workers,
                    on_progress=on_progress,
                    on_context=persist,
                    is_cancelled=budget_reached,
                )
                crawl_result = None
            else:
                extra_query_helps = (
                    session.open_extra_channels(config.discovery.parallel_channels - 1)
                    if config.discovery.parallel_channels > 1
                    else []
                )
                crawl_result = crawl(
                    session.query_help,
                    catalog,
                    seeds,
                    limits,
                    include_root=True,
                    on_progress=on_progress,
                    is_cancelled=budget_reached,
                    extra_query_helps=extra_query_helps,
                    verify_seeds=verify_seeds,
                )
    except (OSError, TimeoutError, RuntimeError, ValueError, paramiko.SSHException) as error:
        raise DeviceConnectionError(f"device session failed: {error}") from error

    written_config: Path | None = None
    config_unexplained = 0
    if config_output:
        apply_config_scan(
            catalog, config, config_command, config_output, _view_prefixes(mode_report)
        )
        written_config = config.output.config_tree
        config_unexplained = _config_unexplained(catalog)

    if mode_report is not None:
        if on_progress:
            on_progress(
                CrawlProgress(
                    queries=0,
                    max_queries=0,
                    prefix="",
                    commands=len(catalog.commands),
                    pending=0,
                    stage="save",
                )
            )
        queries = sum(scan.result.queries for scan in mode_report.scans)
        # Completeness is fail-closed against the running configuration: the
        # crawl may believe it saw everything, but a configured line it never
        # produced proves otherwise, so the device's own state has the last word.
        complete = all(scan.result.complete for scan in mode_report.scans) and (
            config_unexplained == 0
        )
        catalog.scan = {
            "complete": complete,
            "queries": queries,
            "source": "context-graph",
            "config_unexplained": config_unexplained,
            **mode_report.to_dict(),
        }
        write_catalog(catalog, destination)
        write_exports(catalog, config)
        html_destination = config.output.html_report if mode == "compare" else None
        if html_destination is not None:
            write_html_report(catalog, html_destination)
        return ScanOutcome(
            destination,
            html_destination,
            len(catalog.commands),
            queries,
            complete,
            config_path=written_config,
            config_summary=catalog.configuration or None,
        )

    catalog.scan = crawl_result.to_dict()
    catalog.scan["config_unexplained"] = config_unexplained
    if mode == "compare":
        catalog.scan["compare_verify_skipped"] = verify_skipped
    # The device's running configuration overrides an optimistic crawl: a line
    # it could not explain proves the surface is incomplete, so completeness
    # never rests on the firmware's word alone.
    complete = crawl_result.complete and config_unexplained == 0
    catalog.scan["complete"] = complete
    write_catalog(catalog, destination)
    write_exports(catalog, config)
    html_destination = config.output.html_report if mode == "compare" else None
    if html_destination is not None:
        write_html_report(catalog, html_destination)
    return ScanOutcome(
        destination,
        html_destination,
        len(catalog.commands),
        crawl_result.queries,
        complete,
        config_path=written_config,
        config_summary=catalog.configuration or None,
        verify_skipped=verify_skipped,
    )


def _format_eta(pending: int, rate: float) -> str:
    if rate <= 0:
        return "?"
    seconds = round(pending / rate)
    if seconds >= 3600:
        return f"{seconds // 3600}ч {seconds % 3600 // 60}м"
    if seconds >= 60:
        return f"{seconds // 60}м {seconds % 60}с"
    return f"{seconds}с"


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cliradar", description="Map network device CLI commands")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("compare", "audit", "docs"),
        help=(
            "'compare' checks device truth against docs; 'audit' inventories "
            "the device; 'docs' parses documentation without SSH"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=Path("config.yml"))
    parser.add_argument("--docs", type=Path, default=Path("vendor_docs"))
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--enter-modes",
        action="store_true",
        help=(
            "Discover configuration contexts by entering them. Apart from the "
            "read-only discovery.version_commands, this is the only thing that "
            "executes commands on the device; every one is listed in the catalog"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress success output")
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.mode is None and not args.check_config:
        parser.print_usage(sys.stderr)
        print("cliradar: error: choose mode: compare, audit, or docs", file=sys.stderr)
        return ExitCode.USAGE

    progress_shown = False
    # Each stage keeps its own clock: the ETA is meaningless when a fresh
    # context's few queries are divided by the whole run's elapsed time.
    stage_state = {"stage": "", "started": time.monotonic(), "origin": 0, "last": 0}
    line_width = 0
    stage_labels = {
        "crawl": "запросов",
        "verify": "проверка копий",
        "probe": "пробы режимов",
    }

    def show_progress(progress: CrawlProgress) -> None:
        nonlocal progress_shown, line_width
        progress_shown = True
        if stage_state["stage"] != progress.stage or progress.queries < stage_state["last"]:
            stage_state.update(
                stage=progress.stage,
                started=time.monotonic(),
                origin=progress.queries,
            )
        stage_state["last"] = progress.queries
        if progress.stage == "save":
            line = f"запись каталога: {progress.commands} команд..."
        else:
            total = progress.queries + progress.pending
            fraction = progress.queries / total if total else 1.0
            bar = "#" * round(fraction * 20)
            elapsed = time.monotonic() - stage_state["started"]
            done = progress.queries - stage_state["origin"]
            rate = done / elapsed if elapsed > 0 else 0.0
            label = stage_labels.get(progress.stage, progress.stage)
            current = f" | сейчас: {progress.prefix[:40]}" if progress.stage == "probe" else ""
            line = (
                f"[{bar:<20}] {fraction:4.0%}"
                f" | {label}: {progress.queries} | в очереди: {progress.pending}"
                f"{current} | осталось: ~{_format_eta(progress.pending, rate)}"
            )
        # A shorter line must blank what the longer one left behind, or its
        # tail keeps showing ("~0сссс").
        padding = " " * max(0, line_width - len(line))
        line_width = len(line)
        print(f"\r{line}{padding}", end="", file=sys.stderr, flush=True)

    def show_context(scan: object) -> None:
        """Announce a finished context so a long scan is never silent."""
        nonlocal progress_shown, line_width
        context = scan.context  # type: ignore[attr-defined]
        path = " › ".join(context.entry_path) or "корень"
        if progress_shown:
            print(file=sys.stderr)
            progress_shown = False
            line_width = 0
        print(
            f"контекст {context.fingerprint} ({path}):"
            f" {len(scan.catalog.commands)} команд",  # type: ignore[attr-defined]
            file=sys.stderr,
            flush=True,
        )

    try:
        config = load_config(args.config, require_device=args.mode != "docs")
        if args.enter_modes:
            config = replace(
                config,
                discovery=replace(config.discovery, enter_modes=True),
            )
        if args.check_config:
            if not args.quiet:
                print(f"Configuration is valid: {args.config}")
            return ExitCode.OK
        outcome = build_catalog(
            config,
            args.mode,
            args.docs,
            on_progress=None if args.quiet else show_progress,
            on_context=None if args.quiet else show_context,
        )
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return ExitCode.USAGE
    except DeviceConnectionError as error:
        print(f"connection error: {error}", file=sys.stderr)
        return ExitCode.CONNECTION
    except CLIRadarError as error:
        print(f"scan error: {error}", file=sys.stderr)
        return ExitCode.SCAN
    except (OSError, RuntimeError) as error:
        print(f"I/O error: {error}", file=sys.stderr)
        return ExitCode.SCAN
    finally:
        if progress_shown:
            print(file=sys.stderr)
    if not args.quiet:
        if args.mode == "docs":
            print(f"Wrote {outcome.commands} documentation commands to {outcome.catalog_path}")
        else:
            print(
                f"Wrote {outcome.commands} commands to {outcome.catalog_path}"
                f" ({outcome.queries} CLI queries)"
            )
        if outcome.html_path is not None:
            print(f"Wrote HTML report to {outcome.html_path}")
        print(f"Wrote command tree to {config.output.tree_catalog}")
        print(f"Wrote human-readable commands to {config.output.human_catalog}")
        if outcome.verify_skipped:
            print(
                f"note: {outcome.verify_skipped} documented commands were not"
                " verified against the device (compare_verify_limit reached);"
                " they are reported as unverified, not confirmed missing",
                file=sys.stderr,
            )
        if outcome.config_path is not None and outcome.config_summary:
            summary = outcome.config_summary
            print(
                f"Wrote parsed running configuration to {outcome.config_path}"
                f" ({summary['lines']} lines, {float(summary['coverage']):.1%}"
                " explained by the catalog)"
            )
            missing = int(summary["unmatched"])
            if missing:
                # Without --enter-modes the crawl only sees the exec view, while
                # a running configuration is written in configuration-mode
                # syntax - so the lines read as missing because those contexts
                # were never scanned, not because the surface is incomplete.
                if not config.discovery.enter_modes:
                    print(
                        f"warning: {missing} configured lines are not explained;"
                        " the configuration contexts were not scanned - run with"
                        " --enter-modes to catalogue them",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"warning: {missing} configured lines are not in the catalog;"
                        " see 'configuration.missing_from_catalog'",
                        file=sys.stderr,
                    )
    if not outcome.complete:
        print(
            "warning: scan is incomplete; inspect the 'scan' section in the catalog",
            file=sys.stderr,
        )
    return ExitCode.OK


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
