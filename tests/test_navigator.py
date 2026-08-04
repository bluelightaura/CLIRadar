"""Navigator tests driven by a device emulator that reproduces the traps that
break naive CLI crawlers: confirmation dialogs, banners shaped like prompts,
modes that share a prompt, silent logout, hung commands and a root `exit` that
would drop the session.
"""

from __future__ import annotations

import pytest
from fake_device import FakeDevice

from cliradar.navigator import (
    ModeContext,
    ModeNavigator,
    NavigationError,
    fingerprint_of,
    is_probe_allowed,
    mode_fingerprint,
    probe_order,
)


def navigator(device: FakeDevice) -> ModeNavigator:
    nav = ModeNavigator(terminal=device)
    nav.bind_root()
    return nav


# -- fingerprints ---------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("SW1#", "#"),
        ("SW1(config)#", "(config)#"),
        ("SW1(config-vlan-10)#", "(config-vlan-*)#"),
        ("SW1(config-vlan-4094)#", "(config-vlan-*)#"),
        ("switch-01(config-if)>", "(config-if)>"),
        ("  interface description text", None),
    ],
)
def test_mode_fingerprint(line: str, expected: str | None) -> None:
    assert mode_fingerprint(line) == expected


def test_vlan_ids_collapse_to_one_context() -> None:
    assert mode_fingerprint("SW1(config-vlan-10)#") == mode_fingerprint("SW1(config-vlan-20)#")


def test_fingerprint_reads_the_last_prompt() -> None:
    output = "vlan 10\nSW1(config)#\nSW1(config-vlan-10)#"
    assert fingerprint_of(output) == "(config-vlan-*)#"


# -- navigation -----------------------------------------------------------


def test_enters_and_proves_a_nested_context() -> None:
    device = FakeDevice()
    nav = navigator(device)
    context = ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#")

    assert nav.ensure(context) == "(config-vlan-*)#"
    assert device.mode == "config-vlan-10"


def test_leaves_back_to_the_parent() -> None:
    device = FakeDevice()
    nav = navigator(device)
    nav.ensure(ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#"))

    assert nav.leave("(config)#") == "(config)#"
    assert device.mode == "config"


def test_reset_returns_to_root_from_any_depth() -> None:
    device = FakeDevice()
    nav = navigator(device)
    nav.ensure(ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#"))

    assert nav.reset_to_root() == "#"
    assert device.stack == []


def test_root_exit_never_ends_the_session() -> None:
    device = FakeDevice()
    nav = navigator(device)

    nav.reset_to_root()

    assert device.closed is False
    assert "exit" not in device.commands


# -- traps ----------------------------------------------------------------


def test_confirmation_dialog_is_declined_and_state_survives() -> None:
    device = FakeDevice(confirm_commands=frozenset({"vlan 10"}))
    nav = navigator(device)
    nav.ensure(ModeContext("config", "(config)#", ("configure",), "#"))

    _, fingerprint = nav.run("vlan 10")

    assert fingerprint is None  # the dialog left no prompt behind
    assert nav.confirm_fingerprint() == "(config)#"


def test_banner_shaped_like_a_prompt_is_not_mistaken_for_one() -> None:
    device = FakeDevice(banner="Warning: unauthorised access is prohibited #\n")
    nav = navigator(device)

    assert nav.confirm_fingerprint() == "#"
    assert nav.ensure(ModeContext("config", "(config)#", ("configure",), "#")) == "(config)#"


def test_position_check_never_leaves_the_context() -> None:
    # On real hardware Ctrl-C exits configuration mode, so a position check
    # built on it would destroy the state it is measuring.
    device = FakeDevice(interrupt_exits_mode=True)
    nav = navigator(device)
    context = ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#")
    nav.ensure(context)

    for _ in range(3):
        assert nav.confirm_fingerprint() == "(config-vlan-*)#"

    assert device.mode == "config-vlan-10"
    assert device.interrupts == 0


def test_hung_command_is_recovered_by_interrupt() -> None:
    device = FakeDevice(hang_commands=frozenset({"monitor session"}))
    nav = navigator(device)
    nav.run("monitor session")

    assert device.hung is True
    assert nav.confirm_fingerprint() == "#"
    assert device.hung is False


def test_silent_logout_is_repaired_with_a_fresh_channel() -> None:
    device = FakeDevice(idle_logout_after=1)
    nav = navigator(device)
    context = ModeContext("config", "(config)#", ("configure",), "#")

    with pytest.raises(NavigationError):
        nav.ensure(context)

    assert nav.recover() == "#"
    assert device.reopens == 1
    assert nav.ensure(context) == "(config)#"


def test_recovery_survives_any_transport_failure() -> None:
    # paramiko raises AttributeError when its transport is already gone; a
    # scan must not die inside the code meant to rescue it.
    device = FakeDevice()

    def broken_reopen() -> None:
        raise AttributeError("'NoneType' object has no attribute 'open_session'")

    device.reopen = broken_reopen  # type: ignore[method-assign]
    nav = navigator(device)

    with pytest.raises(NavigationError, match="could not be rebuilt"):
        nav.recover()


def test_recover_reports_an_unusable_channel() -> None:
    device = FakeDevice()

    def broken_reopen() -> None:
        device.stack[:] = ["config"]

    device.reopen = broken_reopen  # type: ignore[method-assign]
    nav = navigator(device)

    with pytest.raises(NavigationError, match="fresh channel"):
        nav.recover()


def test_optional_instance_id_does_not_create_a_second_context() -> None:
    # `router ospf` and `router ospf 1` open the same view; scanning it twice
    # doubles the work and duplicates the catalog.
    plain = ModeContext("ospf", "(config-router)#", ("configure", "router ospf"), "(config)#")
    numbered = ModeContext("ospf", "(config-router)#", ("configure", "router ospf 1"), "(config)#")

    assert plain.key == numbered.key


def test_same_prompt_different_entry_stays_two_contexts() -> None:
    ethernet = ModeContext("if-eth", "(config-if)#", ("configure", "interface 10ge1/0/1"), "(config)#")
    vlan = ModeContext("if-vlan", "(config-if)#", ("configure", "interface vlan 10"), "(config)#")

    assert ethernet.fingerprint == vlan.fingerprint
    assert ethernet.key != vlan.key


def test_staying_in_a_context_does_not_re_enter_it() -> None:
    device = FakeDevice()
    nav = navigator(device)
    context = ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#")
    nav.ensure(context)
    entered = list(device.commands)

    nav.ensure(context)
    nav.ensure(context)

    assert device.commands == entered
    assert device.mode == "config-vlan-10"


def test_navigator_records_every_executed_command() -> None:
    device = FakeDevice()
    nav = navigator(device)
    nav.ensure(ModeContext("vlan", "(config-vlan-*)#", ("configure", "vlan 10"), "(config)#"))

    assert nav.executed == ["configure", "vlan 10"]


# -- probe policy ---------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["reboot", "reload in 5", "erase startup-config", "logout", "exit", "ping 192.0.2.1",
     "terminal length 0", "no shutdown", "write memory",
     # The management path: probing here locked a live switch out mid-scan.
     "line vty 1", "line console 1", "username admin", "aaa", "sshd", "service ssh"],
)
def test_dangerous_commands_are_never_probed(command: str) -> None:
    assert is_probe_allowed(command) is False


@pytest.mark.parametrize("command", ["configure", "vlan 10", "interface 10ge1/0/1", "router ospf"])
def test_context_openers_are_probed(command: str) -> None:
    assert is_probe_allowed(command) is True


def test_probe_order_prefers_known_context_openers() -> None:
    ordered = probe_order(
        ["snmp-server enable", "vlan 10", "aaa", "interface 10ge1/0/1"],
        ["vlan", "interface", "aaa"],
    )

    assert ordered[:3] == ["vlan 10", "interface 10ge1/0/1", "aaa"]
