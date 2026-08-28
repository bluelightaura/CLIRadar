"""Tests for harvesting real probe values from the running configuration."""

from __future__ import annotations

from cliradar.harvest import harvest_probe_entries

CONFIG = """\
display current-configuration
#
sysname SW1
#
vlan 100
 name uplink
#
vlan 200
#
interface 10GE1/0/1
 shutdown
#
interface 10GE1/0/2
#
interface 10GE1/0/3
#
line vty 0 4
 idle-timeout 30
#
return
SW1#
"""


def test_top_level_lines_group_by_head_verb() -> None:
    entries = harvest_probe_entries(CONFIG, "display current-configuration")
    assert "vlan 100" in entries["vlan"]
    assert "interface 10GE1/0/1" in entries["interface"]
    assert entries["line"] == ["line vty 0 4"]
    assert "sysname SW1" in entries["sysname"]  # filtering is the scanner's job


def test_nested_lines_are_never_harvested() -> None:
    entries = harvest_probe_entries(CONFIG, "display current-configuration")
    flat = [line for lines in entries.values() for line in lines]
    assert "name uplink" not in flat  # lives inside vlan 100
    assert "shutdown" not in flat  # lives inside an interface
    assert "idle-timeout 30" not in flat


def test_instances_fold_by_shape_and_are_capped() -> None:
    entries = harvest_probe_entries(CONFIG, "display current-configuration")
    # Three interfaces share one digit-folded shape; only two lines survive -
    # the context they open is one and the same, more would be wasted probes.
    assert entries["interface"] == ["interface 10GE1/0/1", "interface 10GE1/0/2"]
    # The two vlans also share a shape.
    assert entries["vlan"] == ["vlan 100"] or entries["vlan"] == [
        "vlan 100", "vlan 200"
    ]


def test_banner_prose_is_not_harvested() -> None:
    config = (
        "#\nheader login information %\n  interface fake prose\n%\n#\nvlan 7\n"
    )
    entries = harvest_probe_entries(config)
    assert "interface" not in entries  # the banner line stayed opaque
    assert entries["vlan"] == ["vlan 7"]


def test_empty_config_harvests_nothing() -> None:
    assert harvest_probe_entries("") == {}
