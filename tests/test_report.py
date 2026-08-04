import re
from html.parser import HTMLParser

from cliradar.models import Catalog
from cliradar.report import render_html_report


class _ReportHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.elements.append((tag, dict(attrs)))


def _report_elements(report: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _ReportHTMLParser()
    parser.feed(report)
    return parser.elements


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


def test_search_has_unique_ids_and_only_filters_command_findings() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={
            "complete": False,
            "queries": 9,
            "contexts": [
                {
                    "fingerprint": "#",
                    "entry_path": [],
                    "commands": 3,
                    "complete": True,
                },
                {
                    "fingerprint": "(config)#",
                    "entry_path": ["configure"],
                    "commands": 1,
                    "complete": False,
                },
            ],
        },
        enumerated={""},
    )
    catalog.add("debug", "Debug commands", "cli")
    catalog.add("show missing", "Confirmed missing", "documentation:commands.txt")
    catalog.add("debug unseen", "Not reached", "documentation:commands.txt")
    catalog.configuration = {
        "source_command": "show running-config",
        "lines": 2,
        "matched": 1,
        "matched_elsewhere": 0,
        "unmatched": 1,
        "coverage": 0.5,
        "missing_from_catalog": [
            {"command": "interface <value> mystery", "occurrences": 1},
        ],
    }

    report = render_html_report(catalog)
    elements = _report_elements(report)
    ids = [attrs["id"] for _, attrs in elements if attrs.get("id")]
    command_rows = [
        attrs
        for tag, attrs in elements
        if tag == "tr" and "data-command-row" in attrs
    ]
    all_rows = [attrs for tag, attrs in elements if tag == "tr"]

    assert len(ids) == len(set(ids))
    assert ids.count("search") == 1
    assert ids.count("counter") == 1
    assert ids.count("clear-search") == 1
    assert len(command_rows) == 2
    assert all("data-text" in attrs for attrs in command_rows)
    assert len(all_rows) > len(command_rows)
    assert re.search(
        r"querySelectorAll\((['\"])\[data-command-row\]\1\)", report
    )
    assert "querySelectorAll('tbody tr')" not in report
    assert 'querySelectorAll("tbody tr")' not in report


def test_search_and_tables_expose_accessible_names_and_live_results() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 2},
    )
    catalog.add("show missing", "Missing command", "documentation:commands.txt")

    elements = _report_elements(render_html_report(catalog))
    searches = [
        attrs
        for tag, attrs in elements
        if tag == "input" and attrs.get("type") == "search"
    ]
    counters = [attrs for _, attrs in elements if attrs.get("id") == "counter"]
    headings = [attrs for tag, attrs in elements if tag == "th"]
    element_ids = {attrs["id"] for _, attrs in elements if attrs.get("id")}

    assert searches == [
        {
            **searches[0],
            "aria-label": "Поиск по командам и описаниям",
            "aria-controls": "command-findings",
        }
    ]
    assert "command-findings" in element_ids
    assert len(counters) == 1
    assert counters[0].get("aria-live") == "polite"
    assert headings
    assert all(attrs.get("scope") == "col" for attrs in headings)


def test_complete_compare_without_missing_commands_has_an_explicit_success_state() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 2},
    )
    catalog.add("show version", "Software version", "cli")
    catalog.add("show version", "Software version", "documentation:commands.txt")

    report = render_html_report(catalog)

    assert 'data-scan-state="complete"' in report
    assert "Подтверждённых расхождений не найдено" in report
    assert "Обход неполный" not in report


def test_incomplete_compare_without_unobserved_commands_is_not_shown_as_successful() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": False, "queries": 2},
    )
    catalog.add("show version", "Software version", "cli")
    catalog.add("show version", "Software version", "documentation:commands.txt")

    payload = catalog.to_dict()
    assert payload["summary"]["not_observed"] == 0

    report = render_html_report(catalog)

    assert 'data-scan-state="incomplete"' in report
    assert "Обход неполный" in report
    assert "результат нельзя считать исчерпывающим" in report
    assert "Подтверждённых расхождений не найдено" not in report


def test_configuration_gap_prevents_a_global_success_state() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True, "queries": 2},
    )
    catalog.add("show version", "Software version", "cli")
    catalog.add("show version", "Software version", "documentation:commands.txt")
    catalog.configuration = {
        "source_command": "show running-config",
        "lines": 1,
        "matched": 0,
        "matched_elsewhere": 0,
        "unmatched": 1,
        "coverage": 0.0,
        "missing_from_catalog": [
            {"command": "interface <value> mystery", "occurrences": 1},
        ],
    }

    report = render_html_report(catalog)

    assert 'data-scan-state="complete"' in report
    assert "конфигурация выявила пробелы каталога" in report
    assert "Подтверждённых расхождений не найдено" not in report
