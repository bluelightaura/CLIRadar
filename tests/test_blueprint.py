"""The expected-blocks template and the four states a tree node can be in."""

from __future__ import annotations

from cliradar.blueprint import (
    ABSENT,
    BLOCKS,
    PARSED,
    STATE_MARKS,
    TOPPED,
    UNKNOWN,
    blueprint_state,
    missing_blocks,
    node_state,
    rejected_verbs,
)


def test_every_state_has_a_mark() -> None:
    assert set(STATE_MARKS) == {UNKNOWN, TOPPED, PARSED, ABSENT}
    assert len(set(STATE_MARKS.values())) == 4  # four states, four glyphs


def test_node_state_reads_the_recorded_numbers() -> None:
    assert node_state(commands=12, complete=True) == PARSED
    # A skim records the commands directly in the context and nothing below it.
    assert node_state(commands=12, complete=False) == TOPPED
    # Named by a probe, never opened.
    assert node_state(commands=0, complete=False) == UNKNOWN


def test_a_complete_context_is_parsed_even_with_no_commands() -> None:
    # An empty context that was crawled to the end is known, not unlooked-at.
    assert node_state(commands=0, complete=True) == PARSED


def test_blocks_cover_the_usual_switch_furniture() -> None:
    labels = {block.label for block in BLOCKS}
    assert {"interface", "vlan", "vrf", "mlag", "line", "router"} <= labels


def test_a_block_matches_every_vendor_spelling() -> None:
    line = next(block for block in BLOCKS if block.label == "line")
    assert line.matches("line")
    assert line.matches("user-interface")  # VRP calls it this
    assert not line.matches("interface")


def test_missing_blocks_skips_what_the_map_already_shows() -> None:
    missing = dict(missing_blocks(found=["vlan", "interface 10ge1/0/1"]))
    labels = {block.label for block in missing}
    assert "vlan" not in labels  # the real thing is on the map already
    assert "interface" not in labels
    assert "vrf" in labels


def test_a_refused_probe_marks_a_block_absent() -> None:
    missing = dict(missing_blocks(found=[], rejected=["mlag"]))
    states = {block.label: state for block, state in missing.items()}
    assert states["mlag"] == ABSENT  # the device was asked and said no
    assert states["vrf"] == UNKNOWN  # never asked


def test_blueprint_state_needs_a_matching_verb() -> None:
    vrf = next(block for block in BLOCKS if block.label == "vrf")
    assert blueprint_state(vrf, ["vlan"]) == UNKNOWN
    assert blueprint_state(vrf, ["vpn-instance"]) == ABSENT


def test_rejected_verbs_counts_only_refusals() -> None:
    probes = [
        {"command": "mlag", "outcome": "rejected"},
        {"command": "vlan 100", "outcome": "entered"},
        {"command": "ntp", "outcome": "reset"},
        {"command": "", "outcome": "rejected"},
    ]
    # "entered" proves presence, "reset" proves only that the session died -
    # neither is evidence that a block is missing.
    assert rejected_verbs(probes) == {"mlag"}
