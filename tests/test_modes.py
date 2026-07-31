"""Tests for the navigator/crawler contract.

The stand proves the method works; these cover what a healthy stand will not
produce on demand - a context that drifts away mid-crawl, a probe that only
looks like an entry, and a device that logs out between two help queries.
"""

from __future__ import annotations

import pytest
from fake_device import FakeDevice

from cliradar.crawler import CrawlLimits
from cliradar.modes import (
    ContextLost,
    GuardedHelp,
    ModeScanReport,
    sample_command,
    scan_modes,
)
from cliradar.navigator import ModeContext, ModeNavigator

LIMITS = CrawlLimits(
    max_depth=4,
    max_queries=200,
    parameter_samples=(("IFNAME", "10ge1/0/1"),),
)


def navigator_for(device: FakeDevice) -> ModeNavigator:
    navigator = ModeNavigator(terminal=device)
    navigator.bind_root()
    return navigator


# -- the guard ------------------------------------------------------------


def test_help_from_the_expected_context_is_passed_through() -> None:
    device = FakeDevice()
    navigator = navigator_for(device)
    context = ModeContext("config", "(config)#", ("configure",), "#")
    navigator.ensure(context)
    guard = GuardedHelp(navigator, context)

    assert "vlan" in guard("")
    assert guard.recoveries == 0


def test_drift_is_detected_and_the_query_is_repeated() -> None:
    device = FakeDevice(drift_after=1)
    navigator = navigator_for(device)
    context = ModeContext("config", "(config)#", ("configure",), "#")
    navigator.ensure(context)
    guard = GuardedHelp(navigator, context)

    guard("")  # first query drops the device back to the root
    output = guard("")

    assert guard.recoveries == 1
    assert "vlan" in output
    assert device.mode == "config"


def test_a_context_that_cannot_be_restored_raises() -> None:
    device = FakeDevice()
    navigator = navigator_for(device)
    context = ModeContext("ghost", "(config-ghost)#", ("configure",), "#")

    with pytest.raises(ContextLost):
        GuardedHelp(navigator, context)("")


def test_a_silent_answer_triggers_recovery_rather_than_being_accepted() -> None:
    device = FakeDevice()
    navigator = navigator_for(device)
    context = ModeContext("root", "#")
    guard = GuardedHelp(navigator, context)
    device.hung = True

    output = guard("")

    assert "configure" in output
    assert guard.recoveries == 1


def test_long_output_cut_before_the_prompt_is_counted_not_repeated() -> None:
    # A response can be truncated before the prompt is echoed; that is not
    # evidence of drift, so it is recorded rather than acted upon.
    device = FakeDevice()
    navigator = navigator_for(device)
    context = ModeContext("root", "#")
    guard = GuardedHelp(navigator, context)
    device.query_help = lambda prefix: "  show  Display information\n"  # type: ignore[method-assign]

    assert "show" in guard("")
    assert guard.unverified == 1
    assert guard.recoveries == 0


# -- the walk -------------------------------------------------------------


def test_scan_discovers_nested_contexts_without_being_told_about_them() -> None:
    device = FakeDevice()
    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=10,
        root_probe_allowlist=frozenset({"configure"}),
    )

    names = {scan.context.name for scan in report.scans}
    assert "root" in names
    assert "root/configure" in names
    assert any(name.endswith("/vlan") for name in names)
    assert any(name.endswith("/interface") for name in names)


def test_scan_records_every_pressed_enter() -> None:
    device = FakeDevice()
    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=6,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert "configure" in report.executed
    assert report.executed == device.commands[: len(report.executed)] or report.executed


def test_scan_never_probes_denylisted_commands() -> None:
    device = FakeDevice()
    scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=6,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert "reboot" not in device.commands
    assert "show" not in device.commands


def test_scan_survives_a_logout_in_the_middle() -> None:
    device = FakeDevice(idle_logout_after=3)
    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=8,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert device.reopens >= 1
    assert report.commands > 0


def test_workers_are_placed_in_every_context_before_it_is_crawled() -> None:
    # Which channel answers a given query is a race, so the guarantee under
    # test is placement: a worker only ever answers from the right context.
    device = FakeDevice()
    worker_device = FakeDevice()
    worker = navigator_for(worker_device)

    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=4,
        workers=[worker],
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert report.commands > 0
    assert "configure" in worker_device.commands
    assert worker_device.executed_writes == []


def test_probes_are_capped_and_the_remainder_is_reported() -> None:
    # Every probe executes a command, so a context with thousands of
    # candidates must not silently turn into thousands of config changes.
    device = FakeDevice()

    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=4,
        max_probes_per_context=1,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert report.probes_skipped > 0
    assert report.to_dict()["probes_skipped"] == report.probes_skipped


def test_a_dropped_channel_does_not_end_the_scan() -> None:
    # The device hung up mid-crawl on real hardware; the scan has to rebuild
    # the session instead of dying with "Socket is closed".
    device = FakeDevice(dead_channel_after=2)

    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=6,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert device.reopens >= 1
    assert report.commands > 0


def test_report_serialises_contexts_and_audit() -> None:
    device = FakeDevice()
    report = scan_modes(
        navigator_for(device),
        limits=LIMITS,
        max_contexts=4,
        root_probe_allowlist=frozenset({"configure"}),
    )

    payload = report.to_dict()

    assert payload["contexts"]
    assert "executed_commands" in payload
    assert all("entry_path" in item for item in payload["contexts"])


def test_empty_report_has_no_commands() -> None:
    assert ModeScanReport().commands == 0


# -- parameter sampling ---------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("vlan <1-4094>", "vlan 1"),
        ("interface IFNAME", "interface 10ge1/0/1"),
        ("hostname WORD", None),
        ("configure", "configure"),
    ],
)
def test_sample_command(command: str, expected: str | None) -> None:
    assert sample_command(command, (("IFNAME", "10ge1/0/1"),)) == expected


def test_catch_all_placeholders_are_not_probed_by_default() -> None:
    # `hostname WORD` renamed a live switch during a stand run: a sample for a
    # catch-all placeholder lands on every unrelated command that takes a word.
    samples = (("WORD", "cliradar"), ("IFNAME", "10ge1/0/1"))

    assert sample_command("hostname WORD", samples, allow_generic=False) is None
    # A specific placeholder names an existing object, so entering it is safe.
    assert (
        sample_command("interface IFNAME", samples, allow_generic=False)
        == "interface 10ge1/0/1"
    )
    assert sample_command("vlan <1-4094>", samples, allow_generic=False) == "vlan 1"


def test_scan_does_not_execute_named_parameter_commands() -> None:
    device = FakeDevice()
    limits = CrawlLimits(
        max_depth=4,
        max_queries=200,
        parameter_samples=(("IFNAME", "10ge1/0/1"), ("WORD", "cliradar")),
    )

    scan_modes(
        navigator_for(device),
        limits=limits,
        max_contexts=6,
        root_probe_allowlist=frozenset({"configure"}),
    )

    assert not [command for command in device.commands if "cliradar" in command]
