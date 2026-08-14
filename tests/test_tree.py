"""Tests for the pure context-tree model the audit menu browses."""

from __future__ import annotations

from cliradar.tree import (
    ContextNode,
    build_context_tree,
    estimate_seconds,
    format_duration,
    split_top_blocks,
    subtree_commands,
    subtree_contexts,
    subtree_queries,
)


def _ctx(name, fingerprint, *, commands=0, queries=0, entry_path=(), complete=True):
    return {
        "name": name,
        "fingerprint": fingerprint,
        "entry_path": list(entry_path),
        "commands": commands,
        "queries": queries,
        "complete": complete,
    }


# A small but realistic map: exec root, a config branch, and three config
# sub-modes under it - the vrf/mlag/vlan shape the operator drills into.
SAMPLE = [
    _ctx("root", "#", commands=800, queries=812),
    _ctx("root/interface", "(config-if)#", commands=120, queries=130,
         entry_path=["interface 10ge1/0/1"]),
    _ctx("root/system-view", "(config)#", commands=40, queries=45,
         entry_path=["system-view"]),
    _ctx("root/system-view/vrf", "(config-vrf)#", commands=25, queries=30,
         entry_path=["system-view", "ip vrf red"]),
    _ctx("root/system-view/mlag", "(config-mlag)#", commands=15, queries=18,
         entry_path=["system-view", "mlag"]),
]


def test_build_links_children_by_name_path() -> None:
    root = build_context_tree(SAMPLE)
    assert root.name == "root"
    by_name = {child.name: child for child in root.children}
    assert set(by_name) == {"root/interface", "root/system-view"}
    system = by_name["root/system-view"]
    assert {child.label for child in system.children} == {"vrf", "mlag"}


def test_label_is_last_segment() -> None:
    root = build_context_tree(SAMPLE)
    system = next(c for c in root.children if c.label == "system-view")
    assert system.label == "system-view"
    assert system.children[0].label in {"vrf", "mlag"}


def test_is_config_reads_the_prompt() -> None:
    root = build_context_tree(SAMPLE)
    assert root.is_config is False  # bare "#"
    interface = next(c for c in root.children if c.label == "interface")
    assert interface.is_config is True  # "(config-if)#"


def test_split_top_blocks_separates_exec_from_config() -> None:
    root = build_context_tree(SAMPLE)
    exec_block, config_block = split_top_blocks(root)
    # interface and system-view both open parenthesised prompts -> config side;
    # nothing here heads the exec block but the root itself, which is excluded.
    assert exec_block == []
    assert {node.label for node in config_block} == {"interface", "system-view"}


def test_split_puts_a_bare_prompt_child_in_the_exec_block() -> None:
    contexts = [
        _ctx("root", "#"),
        _ctx("root/show", ">", entry_path=["show"]),  # still exec: bare prompt
        _ctx("root/system-view", "(config)#", entry_path=["system-view"]),
    ]
    exec_block, config_block = split_top_blocks(build_context_tree(contexts))
    assert {n.label for n in exec_block} == {"show"}
    assert {n.label for n in config_block} == {"system-view"}


def test_orphan_context_attaches_to_nearest_present_ancestor() -> None:
    # "root/system-view" was never scanned; its vrf child must still land under
    # the root rather than being dropped or crashing the build.
    contexts = [
        _ctx("root", "#"),
        _ctx("root/system-view/vrf", "(config-vrf)#",
             entry_path=["system-view", "ip vrf red"]),
    ]
    root = build_context_tree(contexts)
    assert [child.name for child in root.children] == ["root/system-view/vrf"]


def test_subtree_aggregates_cover_descendants() -> None:
    root = build_context_tree(SAMPLE)
    system = next(c for c in root.children if c.label == "system-view")
    # system-view (40/45) + vrf (25/30) + mlag (15/18)
    assert subtree_commands(system) == 80
    assert subtree_queries(system) == 93
    assert subtree_contexts(system) == 3


def test_estimate_seconds_scales_with_transport() -> None:
    root = build_context_tree(SAMPLE)
    system = next(c for c in root.children if c.label == "system-view")
    assert estimate_seconds(system, "ssh") == 93 * 0.15
    assert estimate_seconds(system, "telnet") == 93 * 0.30
    # An unknown transport falls back to the ssh-ish default rather than crash.
    assert estimate_seconds(system, "serial") == 93 * 0.15


def test_format_duration_reads_as_a_badge() -> None:
    assert format_duration(0.4) == "<1s"
    assert format_duration(45) == "~45s"
    assert format_duration(60) == "~1m"
    assert format_duration(150) == "~2m30s"


def test_build_tolerates_missing_fields() -> None:
    # A stripped-down entry (only a name) must not raise: counts default to 0
    # and the fingerprint to None, which reads as a non-config node.
    root = build_context_tree([{"name": "root"}])
    assert isinstance(root, ContextNode)
    assert root.commands == 0
    assert root.is_config is False


def test_build_empty_yields_a_bare_root() -> None:
    root = build_context_tree([])
    assert root.name == "root"
    assert root.children == []
