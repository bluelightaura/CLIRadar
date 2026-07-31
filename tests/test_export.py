from cliradar.export import render_human_yaml, render_tree_yaml
from cliradar.models import Catalog


def _device_catalog() -> Catalog:
    catalog = Catalog(device={"identity": "redacted"}, mode="audit")
    catalog.add("show", "Show running system information", "cli")
    catalog.add("show version", "System version", "cli")
    catalog.add("show ip", "IP information", "cli")
    catalog.add("only documented", "", "documentation:commands.txt")
    return catalog


def test_tree_yaml_nests_tokens_and_skips_documentation() -> None:
    rendered = render_tree_yaml(_device_catalog())

    assert "show:" in rendered
    assert "  version:" in rendered
    assert "  ip:" in rendered
    assert "documented" not in rendered
    assert "Корневых команд: 1" in rendered


def test_human_yaml_maps_commands_to_descriptions() -> None:
    rendered = render_human_yaml(_device_catalog())

    assert "show version: System version" in rendered
    assert "show ip: IP information" in rendered
    assert "documented" not in rendered


def test_docs_mode_exports_documentation_commands() -> None:
    catalog = Catalog(device={"identity": "redacted"}, mode="docs")
    catalog.add("filter", "Packet filter", "documentation:commands.txt")

    assert "filter:" in render_tree_yaml(catalog)
    assert "filter: Packet filter" in render_human_yaml(catalog)
