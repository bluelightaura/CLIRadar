"""Per-block markdown reports rendered from a written catalog."""

from __future__ import annotations

from cliradar.blockreport import (
    MAX_LISTED,
    block_contexts,
    block_label,
    commands_in,
    render_block_report,
    render_block_reports,
)

CONTEXTS = [
    {"name": "root", "fingerprint": "#", "commands": 8, "queries": 8, "complete": True},
    {"name": "root/system-view", "fingerprint": "(config)#",
     "entry_path": ["system-view"], "commands": 4, "queries": 4, "complete": True},
    {"name": "root/system-view/vlan", "fingerprint": "(config-vlan)#",
     "entry_path": ["system-view", "vlan 100"], "commands": 3, "queries": 6,
     "complete": True},
    {"name": "root/system-view/interface", "fingerprint": "(config-if)#",
     "entry_path": ["system-view", "interface 10ge1/0/1"], "commands": 2,
     "queries": 5, "complete": False, "skipped_parameters": ["<1-4094>"]},
]

CATALOG = {
    "scan": {"contexts": CONTEXTS},
    "commands": [
        {"command": "system-view vlan 100 name", "source": ["cli"]},
        {"command": "system-view vlan 100 description", "source": ["cli"]},
        {"command": "system-view interface 10ge1/0/1 shutdown", "source": ["cli"]},
        {"command": "system-view vlan 100 mtu", "source": ["documentation:vrp"]},
    ],
}


def test_block_label_is_the_last_path_segment() -> None:
    assert block_label("root/system-view/vlan") == "vlan"


def test_block_contexts_are_the_config_blocks_not_the_config_root() -> None:
    names = [str(item["name"]) for item in block_contexts(CONTEXTS)]
    # system-view is the container the blocks live in, not a block itself.
    assert names == ["root/system-view/interface", "root/system-view/vlan"]


def test_commands_in_selects_by_entry_path() -> None:
    vlan = next(c for c in CONTEXTS if c["name"].endswith("/vlan"))
    assert commands_in(CATALOG, vlan) == [
        "system-view vlan 100 description",
        "system-view vlan 100 name",
    ]


def test_documented_only_commands_stay_out_of_a_device_block_report() -> None:
    vlan = next(c for c in CONTEXTS if c["name"].endswith("/vlan"))
    # "mtu" is only in the manual; a block report describes the device.
    assert not any("mtu" in command for command in commands_in(CATALOG, vlan))


def test_a_parsed_block_reads_as_complete() -> None:
    vlan = next(c for c in CONTEXTS if c["name"].endswith("/vlan"))
    text = render_block_report(CATALOG, vlan)
    assert text.startswith("# vlan")
    assert "system-view → vlan 100" in text
    assert "parsed" in text
    assert "`system-view vlan 100 name`" in text
    assert "What is missing" not in text


def test_an_incomplete_block_says_what_is_missing() -> None:
    iface = next(c for c in CONTEXTS if c["name"].endswith("/interface"))
    text = render_block_report(CATALOG, iface)
    assert "top only" in text
    assert "What is missing" in text
    assert "<1-4094>" in text  # the sample that would have opened it


def test_children_are_named_in_the_report() -> None:
    parent = next(c for c in CONTEXTS if c["name"].endswith("/vlan"))
    child = {"name": "root/system-view/vlan/acl", "fingerprint": "(config-vlan-acl)#"}
    text = render_block_report(CATALOG, parent, [child])
    assert "blocks opened from here: 1 (acl)" in text


def test_a_long_block_is_capped_with_a_pointer_to_the_catalog() -> None:
    context = {
        "name": "root/system-view/interface",
        "entry_path": ["system-view", "interface 10ge1/0/1"],
        "commands": MAX_LISTED + 5,
        "complete": True,
    }
    catalog = {
        "commands": [
            {"command": f"system-view interface 10ge1/0/1 cmd{index:04d}",
             "source": ["cli"]}
            for index in range(MAX_LISTED + 5)
        ]
    }
    text = render_block_report(catalog, context)
    assert "… and 5 more" in text


def test_every_block_gets_its_own_file() -> None:
    reports = render_block_reports(CATALOG)
    assert set(reports) == {"vlan.md", "interface.md"}
    assert reports["vlan.md"].startswith("# vlan")


def test_two_blocks_with_the_same_label_do_not_overwrite_each_other() -> None:
    contexts = list(CONTEXTS) + [
        {"name": "root/system-view/interface/vlan", "fingerprint": "(config-if-vlan)#",
         "entry_path": ["system-view", "interface 10ge1/0/1", "vlan 100"],
         "commands": 1, "queries": 1, "complete": True},
    ]
    reports = render_block_reports({"scan": {"contexts": contexts}, "commands": []})
    assert len(reports) == 3  # nothing was silently replaced


def test_a_catalog_without_a_scan_yields_no_reports() -> None:
    assert render_block_reports({"commands": []}) == {}
    assert render_block_reports({"scan": {"contexts": "broken"}}) == {}
