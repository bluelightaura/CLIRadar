from cliradar.models import Catalog
from cliradar.report import render_html_report


def test_compare_report_lists_only_confirmed_missing_commands() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 7},
    )
    catalog.add("show present", "", "cli")
    catalog.add("show present", "", "documentation:commands.txt")
    catalog.add(
        "show <missing>",
        "<script>alert(1)</script>",
        "documentation:commands.txt",
    )

    report = render_html_report(catalog)

    assert "Нет на устройстве <span>1</span>" in report
    assert "show &lt;missing&gt;" in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>alert(1)</script>" not in report
    assert "<code>show present</code>" not in report
    assert 'id="search"' in report
    assert 'id="counter"' in report
    assert "data-text=" in report


def test_incomplete_compare_report_does_not_claim_command_is_missing() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": False, "queries": 3},
    )
    catalog.add("show uncertain", "", "documentation:commands.txt")

    report = render_html_report(catalog)

    assert "Обход не дошёл до этих ветвей" in report
    assert "Не обнаружены <span>1</span>" in report
    assert "Нет на устройстве <span>0</span>" in report


def test_an_incomplete_scan_still_states_an_absence_it_actually_proved() -> None:
    """The root listed its keywords in full, so `show` is absent, not unseen."""
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": False, "queries": 3},
        enumerated={"", "debug"},
    )
    catalog.add("debug", "", "cli")
    catalog.add("debug isis", "", "cli")
    catalog.add("show version", "", "documentation:commands.txt")
    catalog.add("debug banner", "", "documentation:commands.txt")
    catalog.add("debug isis unreached", "", "documentation:commands.txt")

    payload = catalog.to_dict()
    status = {item["command"]: item["comparison_status"] for item in payload["commands"]}

    # The root listed every keyword it has and `show` was not among them.
    assert status["show version"] == "missing_on_device"
    # `debug` was enumerated too, so a missing child of it is equally proven.
    assert status["debug banner"] == "missing_on_device"
    # `debug isis` exists but was never enumerated, so nothing is proven below it.
    assert status["debug isis unreached"] == "not_observed"

    report = render_html_report(catalog)

    assert "Нет на устройстве <span>2</span>" in report
    assert "Не обнаружены <span>1</span>" in report


def test_context_graph_report_shows_modes_and_audits_executed_commands() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="audit",
        scan={
            "complete": True,
            "queries": 42,
            "source": "context-graph",
            "contexts": [
                {"fingerprint": "#", "entry_path": [], "commands": 400, "complete": True},
                {
                    "fingerprint": "(config-vlan-*)#",
                    "entry_path": ["configure", "vlan 1"],
                    "commands": 12,
                    "complete": False,
                    "skipped_parameters": ["WORD", "A.B.C.D"],
                    "derived_mismatched": ["router ospf area"],
                },
            ],
            "executed_commands": ["configure", "vlan 1", "<script>alert(1)</script>"],
            "channel_reopens": 2,
        },
    )
    catalog.add("configure vlan 1 name", "", "cli")

    report = render_html_report(catalog)

    assert "Контексты CLI <span>2</span>" in report
    assert "configure › vlan 1" in report
    assert "корень" in report
    assert "Выполненные команды <span>3</span>" in report
    assert "нет примера для WORD, A.B.C.D" in report
    # A copied subtree the device disagreed with is the loudest reason of all.
    assert "выведенные ветки не подтвердились: 1" in report
    assert "Переоткрытий канала: 2" in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>alert(1)</script>" not in report


def test_report_without_context_graph_has_no_audit_block() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="audit",
        scan={"complete": True, "queries": 1},
    )
    catalog.add("show version", "", "cli")

    report = render_html_report(catalog)

    assert "Контексты CLI" not in report
    assert "Выполненные команды" not in report


def test_audit_report_explains_that_missing_commands_cannot_be_determined() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="audit",
        scan={"complete": True, "queries": 1},
    )
    catalog.add("show version", "", "cli")

    report = render_html_report(catalog)

    assert "audit" in report
    assert "определить отсутствующие команды невозможно" in report


def test_report_states_which_firmware_the_catalog_describes() -> None:
    catalog = Catalog(
        device={
            "identity": "redacted",
            "firmware": {
                "captured_at_start": True,
                "results": [
                    {"command": "show version", "output": "SwitchOS 8.4.2 build 771"},
                ],
            },
        },
        mode="compare",
        scan={"complete": True, "queries": 7},
    )
    catalog.add("show present", "", "cli")

    report = render_html_report(catalog)

    assert "Версия ПО устройства" in report
    assert "SwitchOS 8.4.2 build 771" in report


def test_report_escapes_firmware_output_and_reports_a_failed_capture() -> None:
    catalog = Catalog(
        device={
            "identity": "redacted",
            "firmware": {
                "captured_at_start": True,
                "results": [
                    {"command": "show version", "output": "<script>alert(1)</script>"},
                    {"command": "show system", "error": "timed out"},
                ],
            },
        },
        mode="compare",
        scan={"complete": True, "queries": 7},
    )

    report = render_html_report(catalog)

    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "timed out" in report


def test_report_without_a_firmware_stamp_omits_the_section() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 7},
    )

    assert "Версия ПО устройства" not in render_html_report(catalog)


def test_report_shows_configuration_coverage_and_catalog_gaps() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 3},
    )
    catalog.add("interface IFNAME", "", "cli")
    catalog.add("interface IFNAME", "", "documentation:commands.txt")
    catalog.configuration = {
        "source_command": "display current-configuration",
        "lines": 12,
        "matched": 10,
        "matched_elsewhere": 0,
        "unmatched": 2,
        "free_text": 0,
        "coverage": 0.8333,
        "missing_from_catalog": [
            {"command": "interface <value> sflow sampling-rate <value>", "occurrences": 2},
        ],
    }

    report = render_html_report(catalog)

    assert "Конфигурация против каталога" in report
    assert "sflow sampling-rate" in report
    assert "83.3%" in report
    # The gap table folds values away; a raw configured value never appears.
    assert "4096" not in report


def test_report_without_configuration_omits_the_section() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 1},
    )
    report = render_html_report(catalog)
    assert "Конфигурация против каталога" not in report
