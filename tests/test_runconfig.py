"""Tests for the running-configuration parser and its match against the catalog.

The stand proves the whole thing end to end; these pin the parts a healthy
device will not produce on demand - a banner shaped like configuration, a
secret that must not survive into the report, a configured line the catalog has
never seen, and the same command reached under a different view path.
"""

from __future__ import annotations

from cliradar.models import Catalog
from cliradar.runconfig import (
    CommandTrie,
    correlate,
    parse_config,
    redact_secrets,
    render_config_yaml,
    skeleton_of,
)

CONFIG = """
SW1#display current-configuration
#
sysname SW1
#
vlan 10
 description OFFICE
#
interface 10GE1/0/1
 port link-type access
 port default vlan 10
#
interface 10GE1/0/2
 sflow sampling-rate 4096
#
return
SW1#
"""


def _catalog(*commands: str) -> Catalog:
    catalog = Catalog(device={"identity": "redacted"}, mode="audit")
    for command in commands:
        catalog.add(command, "", "cli")
    return catalog


# -- parsing --------------------------------------------------------------


def test_indentation_becomes_hierarchy() -> None:
    lines = parse_config(CONFIG, "display current-configuration")
    by_text = {line.text: line for line in lines}

    assert by_text["sysname SW1"].depth == 0
    assert by_text["description OFFICE"].path == ("vlan 10", "description OFFICE")
    assert by_text["port default vlan 10"].path[0] == "interface 10GE1/0/1"


def test_separator_closes_the_open_view() -> None:
    lines = parse_config(CONFIG, "display current-configuration")
    # The two interfaces are siblings, not one nested in the other: the "#"
    # between them closed the first before the second began.
    second = next(line for line in lines if line.text == "sflow sampling-rate 4096")
    assert second.path == ("interface 10GE1/0/2", "sflow sampling-rate 4096")


def test_echo_and_terminator_are_dropped() -> None:
    texts = [line.text for line in parse_config(CONFIG, "display current-configuration")]
    assert "display current-configuration" not in texts
    assert "return" not in texts
    assert not any(text.startswith("SW1#") for text in texts)


def test_a_banner_is_one_opaque_line() -> None:
    config = "#\nheader login information %\n  Unauthorised access prohibited.\n    interface fake  (this indented line is prose, not a view)\n%\n#\nsysname SW1"
    lines = parse_config(config)
    banners = [line for line in lines if line.verbatim]
    assert len(banners) == 1
    # The prose between the delimiters never became configuration lines.
    assert not any("Unauthorised" in line.text for line in lines)
    assert any(line.text == "sysname SW1" for line in lines)


def test_unterminated_banner_is_reported_not_swallowed() -> None:
    config = "header login %\n  never closed\nsysname SW1"
    lines = parse_config(config)
    assert any("unterminated" in line.text for line in lines)


# -- redaction ------------------------------------------------------------


def test_secrets_are_blanked_before_anything_else_sees_them() -> None:
    config = "aaa\n local-user admin password irreversible-cipher S3cr3tHash==\n snmp-agent community read Public0Community"
    redacted = redact_secrets(config)
    assert "S3cr3tHash" not in redacted
    assert "Public0Community" not in redacted
    assert "<redacted>" in redacted


def test_skeleton_folds_values_but_keeps_keywords() -> None:
    assert skeleton_of("ip address 192.0.2.1 255.255.255.0") == "ip address <value> <value>"
    assert skeleton_of("port link-type access") == "port link-type access"


def test_word_shaped_secret_is_blanked() -> None:
    # A password or community that carries no digit and is short would slip past
    # a value-folding redaction; it must still be removed.
    redacted = redact_secrets("username admin password cisco\nsnmp-server community public ro")
    assert "cisco" not in redacted
    assert "public" not in redacted
    assert redacted.count("<redacted>") == 2


def test_redaction_blanks_the_value_wherever_it_sits() -> None:
    # The secret is last here (after the "read" qualifier) and first-after-keyword
    # there; both leave nothing after the keyword.
    assert redact_secrets("snmp-agent community read Public0") == "snmp-agent community <redacted>"
    assert redact_secrets("password 7 070C285F") == "password <redacted>"


def test_qualifier_only_line_is_not_mangled() -> None:
    # A bare keyword with no value introduces no secret, and a compound token is
    # not the keyword: neither must be touched.
    assert redact_secrets("authentication-mode md5") == "authentication-mode md5"
    assert redact_secrets("authentication-mode hmac-sha-256") == "authentication-mode hmac-sha-256"


# -- matching against the catalog -----------------------------------------


def test_trie_matches_instance_numbers_and_parameters() -> None:
    trie = CommandTrie()
    trie.add("interface IFNAME")
    trie.add("vlan <1-4094>")
    # A concrete port matches the walked one on another number.
    assert trie.match(("interface", "10GE1/0/24"))
    assert trie.match(("vlan", "10"))
    assert trie.match(("interface",)) is None


def test_configured_line_is_matched_under_its_view_path() -> None:
    catalog = _catalog(
        "system-view",
        "system-view interface IFNAME",
        "system-view interface IFNAME port link-type access",
    )
    lines = parse_config(
        "interface 10GE1/0/1\n port link-type access",
    )
    coverage = correlate(
        lines, catalog, command="display current-configuration",
        view_prefixes=(("system-view",),),
    )
    statuses = {item.line.text: item.status for item in coverage.lines}
    assert statuses["port link-type access"] == "matched"


def test_unknown_line_is_reported_as_a_catalog_gap_without_its_value() -> None:
    catalog = _catalog("interface IFNAME")
    lines = parse_config(
        "interface 10GE1/0/2\n sflow sampling-rate 4096",
    )
    coverage = correlate(lines, catalog, command="display current-configuration")
    summary = coverage.to_dict()
    missing = {item["command"] for item in summary["missing_from_catalog"]}
    assert any("sflow" in command for command in missing)
    # The configured value is folded away - it belongs to the customer.
    assert not any("4096" in command for command in missing)
    assert summary["coverage"] < 1.0


def test_coverage_is_one_when_everything_is_known() -> None:
    catalog = _catalog("sysname WORD", "vlan <1-4094>", "vlan <1-4094> description WORD")
    lines = parse_config(
        "sysname SW1\nvlan 10\n description OFFICE",
    )
    coverage = correlate(lines, catalog)
    assert coverage.to_dict()["coverage"] == 1.0
    assert coverage.count("unmatched") == 0


def test_secret_line_still_matches_the_catalog() -> None:
    # Correlation runs on the real tokens, so a configured secret the catalog
    # explains stays matched and never drags completeness down (finding B).
    catalog = _catalog("snmp-agent community read WORD")
    lines = parse_config("snmp-agent community read s3cr3t")
    coverage = correlate(lines, catalog)
    assert coverage.count("unmatched") == 0
    assert coverage.to_dict()["coverage"] == 1.0


def test_unmatched_secret_line_names_the_command_without_the_value() -> None:
    catalog = _catalog("interface IFNAME")  # nothing about snmp here
    lines = parse_config("snmp-server community topsecretword ro")
    summary = correlate(lines, catalog).to_dict()
    missing = " ".join(str(item["command"]) for item in summary["missing_from_catalog"])
    assert "topsecretword" not in missing
    assert "community" in missing


def test_render_shows_redacted_text_while_correlation_used_the_real_line() -> None:
    catalog = _catalog("snmp-agent community read WORD")
    output = "snmp-agent community read topsecretword"
    coverage = correlate(parse_config(output), catalog)
    rendered = render_config_yaml(parse_config(redact_secrets(output)), coverage)
    assert "topsecretword" not in rendered
    assert "community" in rendered
    # It matched on the real tokens, so it is not flagged as a catalog gap.
    assert "НЕ НАЙДЕНО В КАТАЛОГЕ" not in rendered


def test_render_marks_unknown_lines_in_place() -> None:
    catalog = _catalog("interface IFNAME")
    lines = parse_config("interface 10GE1/0/2\n sflow sampling-rate 4096")
    coverage = correlate(lines, catalog)
    rendered = render_config_yaml(lines, coverage)
    assert "НЕ НАЙДЕНО В КАТАЛОГЕ" in rendered
    assert "sflow sampling-rate 4096" in rendered
