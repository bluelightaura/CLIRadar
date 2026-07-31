import pytest

from cliradar.crawler import CrawlLimits, CrawlProgress, crawl
from cliradar.models import Catalog

HELP = {
    "": "  show     Show running system information\n  reload   Reload system\n",
    "show ": "  version     System version\n  ip          IP information\n",
    "show version ": "  <cr>     Execute command\n",
    "show ip ": "  interface     IP interface status\n",
    "show ip interface ": "  <cr>     Execute command\n",
}


def test_crawls_keywords_and_reports_denied_branch() -> None:
    queried: list[str] = []

    def fake_query(prefix: str) -> str:
        queried.append(prefix)
        return HELP[prefix]

    catalog = Catalog(device={"identity": "redacted"})
    result = crawl(
        fake_query,
        catalog,
        [],
        CrawlLimits(max_depth=5, max_queries=20, denied_tokens=frozenset({"reload"})),
    )

    assert result.queries == 5
    assert result.complete is False
    assert result.skipped_denied == 1
    assert "reload " not in queried
    assert catalog.commands["show version"].executable is True
    assert catalog.commands["show ip interface"].description == "IP interface status"


def test_repeated_shapes_are_copied_instead_of_walked() -> None:
    # Every log level continues identically, so only one of them is walked and
    # the rest are copied - the pattern that dominates real CLI grammars.
    levels = ["alert", "critical", "debugging"]
    help_pages = {"": "  level  Severity\n", "level ": "".join(
        f"  {name}   Level {name}\n" for name in levels
    )}
    for name in levels:
        help_pages[f"level {name} "] = "  syslog  To syslog\n  console  To console\n"
        help_pages[f"level {name} syslog "] = "  <cr>  Execute\n"
        help_pages[f"level {name} console "] = "  <cr>  Execute\n"
    queried: list[str] = []

    def fake_query(prefix: str) -> str:
        queried.append(prefix)
        return help_pages.get(prefix, "  <cr>  Execute\n")

    catalog = Catalog(device={"identity": "redacted"})
    result = crawl(fake_query, catalog, [], CrawlLimits(max_depth=6, max_queries=100))

    for name in levels:
        assert catalog.commands[f"level {name} syslog"].executable is True
        assert catalog.commands[f"level {name} console"].executable is True
    assert result.derived_nodes == 2
    assert result.derived_verified == 2
    assert result.derived_mismatched == ()
    assert queried.count("level critical syslog ") == 0


def test_copied_subtrees_respect_the_depth_limit() -> None:
    # A derived node sits deeper than its canonical, so a blind copy would
    # produce commands past the bound the caller asked for.
    help_pages = {
        "": "  a  Shallow twin\n  x  Deep path\n",
        "a ": "  m  One\n",
        "a m ": "  n  Two\n",
        "a m n ": "  o  Three\n",
        "x ": "  y  Next\n",
        "x y ": "  b  Deep twin\n",
        "x y b ": "  m  One\n",
    }

    catalog = Catalog(device={"identity": "redacted"})
    crawl(
        lambda prefix: help_pages.get(prefix, "  <cr>  Execute\n"),
        catalog,
        [],
        CrawlLimits(max_depth=5, max_queries=100),
    )

    assert max(len(command.split()) for command in catalog.commands) <= 5


def test_a_derived_subtree_that_does_not_match_is_reported() -> None:
    responses = {
        "": "  a  First\n  b  Second\n",
        "a ": "  x  Option\n",
        "b ": "  x  Option\n",
        "a x ": "  <cr>  Execute\n",
    }
    # `b` looks like `a` while walking, but answers differently when verified.
    verification = {"b ": "  y  Different\n"}
    seen: set[str] = set()

    def fake_query(prefix: str) -> str:
        if prefix in seen and prefix in verification:
            return verification[prefix]
        seen.add(prefix)
        return responses.get(prefix, "  <cr>  Execute\n")

    catalog = Catalog(device={"identity": "redacted"})
    result = crawl(fake_query, catalog, [], CrawlLimits(max_depth=5, max_queries=50))

    assert result.derived_mismatched == ("b",)
    assert result.complete is False


def test_deduplication_can_be_switched_off() -> None:
    help_pages = {
        "": "  a  First\n  b  Second\n",
        "a ": "  x  Option\n",
        "b ": "  x  Option\n",
    }
    queried: list[str] = []

    def fake_query(prefix: str) -> str:
        queried.append(prefix)
        return help_pages.get(prefix, "  <cr>  Execute\n")

    catalog = Catalog(device={"identity": "redacted"})
    crawl(
        fake_query,
        catalog,
        [],
        CrawlLimits(max_depth=5, max_queries=50, deduplicate_subtrees=False),
    )

    assert "b x " in queried


def test_option_flags_are_catalogued_but_not_expanded() -> None:
    # A utility with independent flags would otherwise generate every
    # combination of them, and none of those prefixes is a new command.
    help_pages = {
        "": "  ping     Send echo requests\n",
        "ping ": "  A.B.C.D   Target address\n",
        "ping 192.0.2.1 ": "  -q   Quiet output\n  -c   Count\n  <cr>  Execute\n",
    }
    queried: list[str] = []

    def fake_query(prefix: str) -> str:
        queried.append(prefix)
        return help_pages.get(prefix, "  <cr>  Execute\n")

    catalog = Catalog(device={"identity": "redacted"})
    crawl(
        fake_query,
        catalog,
        [],
        CrawlLimits(
            max_depth=6,
            max_queries=50,
            parameter_samples=(("A.B.C.D", "192.0.2.1"),),
        ),
    )

    assert "ping <A.B.C.D> -q" in catalog.commands or "ping A.B.C.D -q" in catalog.commands
    assert not [prefix for prefix in queried if "-q" in prefix]
    assert not [prefix for prefix in queried if "-c" in prefix]


def test_reports_progress_and_honors_cancellation() -> None:
    progress: list[CrawlProgress] = []
    catalog = Catalog(device={"host": "test"})

    result = crawl(
        lambda prefix: HELP[prefix],
        catalog,
        [],
        CrawlLimits(max_depth=5, max_queries=20),
        on_progress=progress.append,
        is_cancelled=lambda: len(progress) >= 1,
    )

    assert result.queries == 1
    assert result.cancelled is True
    assert progress[0].queries == 1
    assert progress[0].commands == 2


def test_compare_scans_full_device_tree_and_marks_differences() -> None:
    queried: list[str] = []
    responses = {
        "": "  show     Show information\n  reload   Reload system\n",
        "show ": "  version     System version\n",
        "show version ": "  <cr>     Execute command\n",
        "reload ": "  <cr>     Execute command\n",
        "show missing ": "% Invalid input detected\n",
    }
    catalog = Catalog(device={"identity": "redacted"}, mode="compare")
    catalog.add("show version", "", "documentation:commands.txt")
    catalog.add("show missing", "", "documentation:commands.txt")

    result = crawl(
        lambda prefix: queried.append(prefix) or responses.get(prefix, ""),
        catalog,
        ["show version", "show missing"],
        CrawlLimits(max_depth=5, max_queries=20),
    )
    catalog.scan = result.to_dict()
    commands = {item["command"]: item for item in catalog.to_dict()["commands"]}

    assert queried[0] == ""
    assert result.complete is True
    assert commands["show version"]["comparison_status"] == "matched"
    assert commands["reload"]["comparison_status"] == "undocumented"
    assert commands["show missing"]["comparison_status"] == "missing_on_device"


def test_compare_does_not_claim_missing_after_incomplete_scan() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": False},
    )
    catalog.add("show missing", "", "documentation:commands.txt")

    command = catalog.to_dict()["commands"][0]

    assert command["comparison_status"] == "not_observed"


def test_uses_numeric_sample_but_keeps_parameter_in_catalog() -> None:
    queried: list[str] = []
    responses = {
        "": "  show          Show information\n",
        "show ": "  vlan          VLAN information\n",
        "show vlan ": "  <1-4094>      VLAN identifier\n",
        "show vlan 1 ": "  brief         Brief output\n",
        "show vlan 1 brief ": "  <cr>          Execute command\n",
    }
    catalog = Catalog(device={"identity": "redacted"})

    result = crawl(
        lambda prefix: queried.append(prefix) or responses[prefix],
        catalog,
        [],
        CrawlLimits(max_depth=8, max_queries=20),
    )

    assert result.complete is True
    assert "show vlan 1 " in queried
    assert "show vlan <1-4094> brief" in catalog.commands
    assert catalog.commands["show vlan <1-4094> brief"].executable is True


def test_uses_configured_sample_for_named_parameter() -> None:
    queried: list[str] = []
    responses = {
        "show interface ": "  WORD          Interface name\n",
        "show interface Ethernet1/1 ": "  counters      Interface counters\n",
        "show interface Ethernet1/1 counters ": "  <cr>          Execute command\n",
    }
    catalog = Catalog(device={"identity": "redacted"})

    result = crawl(
        lambda prefix: queried.append(prefix) or responses[prefix],
        catalog,
        ["show interface"],
        CrawlLimits(
            max_depth=8,
            max_queries=20,
            parameter_samples=(("WORD", "Ethernet1/1"),),
        ),
        include_root=False,
    )

    assert result.complete is True
    assert queried[1] == "show interface Ethernet1/1 "
    assert "show interface WORD counters" in catalog.commands


def test_seed_uses_sample_in_query_and_placeholder_in_catalog() -> None:
    queried: list[str] = []
    catalog = Catalog(device={"identity": "redacted"}, mode="compare")
    catalog.add(
        "show vlan <1-4094>",
        "",
        "documentation:commands.txt",
    )

    result = crawl(
        lambda prefix: queried.append(prefix) or "  <cr>     Execute command\n",
        catalog,
        ["show vlan <1-4094>"],
        CrawlLimits(max_depth=8, max_queries=20),
        include_root=False,
    )

    assert queried == ["show vlan 1 "]
    assert result.queries == 1
    assert catalog.commands["show vlan <1-4094>"].on_device is True


def test_seed_uses_configured_sample_for_lowercase_document_placeholder() -> None:
    queried: list[str] = []
    catalog = Catalog(device={"identity": "redacted"}, mode="compare")
    catalog.add(
        "show interface interface-name",
        "",
        "documentation:commands.txt",
    )

    result = crawl(
        lambda prefix: queried.append(prefix) or "  <cr>     Execute command\n",
        catalog,
        ["show interface interface-name"],
        CrawlLimits(
            max_depth=8,
            max_queries=20,
            parameter_samples=(("interface-name", "Ethernet1/1"),),
        ),
        include_root=False,
    )

    assert queried == ["show interface Ethernet1/1 "]
    assert result.complete is True
    assert catalog.commands["show interface interface-name"].on_device is True


def test_reports_incomplete_scan_when_query_limit_is_reached() -> None:
    catalog = Catalog(device={"identity": "redacted"})

    result = crawl(
        lambda prefix: HELP[prefix],
        catalog,
        [],
        CrawlLimits(max_depth=5, max_queries=1),
    )

    assert result.complete is False
    assert result.query_limit_reached is True
    assert result.pending_nodes == 2


def test_verify_seeds_confirm_presence_without_expanding_children() -> None:
    queried: list[str] = []
    responses = {
        "": "  show     Show information\n",
        "show ": "  version     System version\n",
        "show version ": "  <cr>     Execute command\n",
        "filter ": "  udp     Match UDP\n  tcp     Match TCP\n",
    }
    catalog = Catalog(device={"identity": "redacted"}, mode="compare")
    catalog.add("filter", "", "documentation:commands.txt")

    result = crawl(
        lambda prefix: queried.append(prefix) or responses.get(prefix, ""),
        catalog,
        [],
        CrawlLimits(max_depth=8, max_queries=50),
        verify_seeds=["filter"],
    )

    assert "filter " in queried
    assert "filter udp " not in queried
    assert "filter udp" not in catalog.commands
    assert catalog.commands["filter"].on_device is True
    assert result.complete is True


def test_parallel_crawl_matches_sequential_catalog() -> None:
    limits = CrawlLimits(max_depth=5, max_queries=20, denied_tokens=frozenset({"reload"}))
    sequential = Catalog(device={"identity": "redacted"})
    crawl(lambda prefix: HELP[prefix], sequential, [], limits)

    parallel = Catalog(device={"identity": "redacted"})
    result = crawl(
        lambda prefix: HELP[prefix],
        parallel,
        [],
        limits,
        extra_query_helps=[lambda prefix: HELP[prefix]] * 3,
    )

    assert result.queries == 5
    assert result.skipped_denied == 1
    assert set(parallel.commands) == set(sequential.commands)
    assert parallel.commands["show version"].executable is True


def test_parallel_crawl_propagates_worker_errors() -> None:
    def broken(prefix: str) -> str:
        raise TimeoutError("device went away")

    catalog = Catalog(device={"identity": "redacted"})

    with pytest.raises(TimeoutError):
        crawl(
            broken,
            catalog,
            [],
            CrawlLimits(max_depth=5, max_queries=20),
            extra_query_helps=[broken],
        )


def test_negation_mirror_is_crawled_last() -> None:
    # The `no`/`undo` branch mirrors the whole tree without describing any new
    # capability, so a scan cut short by a limit loses the mirror rather than
    # the commands. Ordinary branches must all be asked before the first
    # negation prefix is.
    answers = {
        "": (
            "  show  Display\n  vlan  VLAN\n  no  Negate a command\n"
            "  undo  Undo a command\n  <cr>\n"
        ),
        "show ": "  version  Version\n  <cr>\n",
        "vlan ": "  <cr>\n",
        "show version ": "  <cr>\n",
        "no ": "  vlan  VLAN\n  <cr>\n",
        "no vlan ": "  <cr>\n",
        "undo ": "  <cr>\n",
    }
    order: list[str] = []

    def query_help(prefix: str) -> str:
        order.append(prefix)
        return answers.get(prefix, "  <cr>\n")

    catalog = Catalog(device={}, mode="audit")
    result = crawl(query_help, catalog, [], CrawlLimits(max_depth=6, max_queries=100))

    assert result.complete
    first_negation = min(order.index("no "), order.index("undo "))
    ordinary = [prefix for prefix in order if not prefix.startswith(("no", "undo"))]
    assert all(order.index(prefix) < first_negation for prefix in ordinary)
    # The mirror is still fully crawled - deferred, never dropped.
    assert "no vlan" in catalog.commands
