from pathlib import Path

import pytest

from cliradar.docs import MAX_DOCUMENT_BYTES, scan_documentation


def test_scan_documentation_reports_per_file_progress(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("interface GigabitEthernet0/1\n")
    (tmp_path / "b.md").write_text("```\nshow version\n```\n")
    ticks: list[tuple[int, int, str]] = []
    scan_documentation(tmp_path, on_progress=lambda i, t, n: ticks.append((i, t, n)))
    # One tick per file, then a final full tick that closes the bar.
    assert ticks[0] == (0, 2, "a.txt")
    assert ticks[1] == (1, 2, "b.md")
    assert ticks[-1] == (2, 2, "")


def test_scan_documentation_progress_is_optional(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("show version\n")
    # No callback must not raise and must still return commands.
    assert scan_documentation(tmp_path)


def test_extracts_commands_from_fences_and_tables(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        """
| Command | Description |
| `show version` | Displays software version |

```text
switch# show interfaces status
configure terminal
```
""",
        encoding="utf-8",
    )

    commands = scan_documentation(tmp_path)

    assert set(commands) == {"show version", "show interfaces status", "configure terminal"}
    assert commands["show version"].description == "Displays software version"


def test_skips_symlinks_and_oversized_documents(tmp_path: Path) -> None:
    # The suffix keeps the target itself out of the scan, so any command
    # showing up can only have come through the symlink or the oversized file.
    target = tmp_path / "target.log"
    target.write_text("show secrets\n", encoding="utf-8")
    try:
        (tmp_path / "linked.txt").symlink_to(target)
    except OSError:
        pytest.skip("creating symlinks requires additional privileges on this platform")
    (tmp_path / "large.txt").write_bytes(b"show oversized\n" + b"x" * MAX_DOCUMENT_BYTES)

    assert scan_documentation(tmp_path) == {}


def test_extracts_plain_text_command_lists_and_labeled_syntax(tmp_path: Path) -> None:
    (tmp_path / "commands.txt").write_text(
        """
# Commands copied from the vendor reference
show version
show interfaces status  Displays interface state
Syntax: show vlan <1-4094>
switch# show ip route
This sentence is documentation, not a command.
""",
        encoding="utf-8",
    )

    commands = scan_documentation(tmp_path)

    assert set(commands) == {
        "show version",
        "show interfaces status",
        "show vlan <1-4094>",
        "show ip route",
    }
    assert commands["show interfaces status"].description == "Displays interface state"


def test_prefers_command_format_blocks_over_prose_and_expands_grammar(
    tmp_path: Path,
) -> None:
    (tmp_path / "reference.txt").write_text(
        """
The command reference contains prose that must not become a command.

Command Format
show route { ipv4 | ipv6 } [ brief ]
Parameter Description
brief displays a short result.

Command Format
command-privilege view { exec |
configure } [ COMMAND ]
Parameter
COMMAND specifies a command string.

Command Format
description WORD
Parameter
WORD specifies interface description.
""",
        encoding="utf-8",
    )

    commands = scan_documentation(tmp_path)

    assert set(commands) == {
        "show route ipv4",
        "show route ipv4 brief",
        "show route ipv6",
        "show route ipv6 brief",
        "command-privilege view exec",
        "command-privilege view exec COMMAND",
        "command-privilege view configure",
        "command-privilege view configure COMMAND",
        "description WORD",
    }


def test_supports_russian_and_labeled_syntax_headings(tmp_path: Path) -> None:
    (tmp_path / "reference.txt").write_text(
        """
Формат команды
display interface { brief | verbose }
Параметр
brief — краткий вывод.
""",
        encoding="utf-8",
    )

    commands = scan_documentation(tmp_path)

    assert set(commands) == {
        "display interface brief",
        "display interface verbose",
    }


def test_skips_reference_text_when_conversion_destroyed_heading_spacing(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken-reference.txt").write_text(
        """
Форматкоманды
showversion
Описаниепараметров
this prose must not become a command
""",
        encoding="utf-8",
    )

    assert scan_documentation(tmp_path) == {}


def test_matches_negated_syntax_to_negated_section_heading(tmp_path: Path) -> None:
    (tmp_path / "reference.txt").write_text(
        """
4.2 no logging
Command Format
no logging host
Description
Disables a logging destination.
""",
        encoding="utf-8",
    )

    assert set(scan_documentation(tmp_path)) == {"no logging host"}


def test_supports_split_russian_sections_and_bulleted_syntax(tmp_path: Path) -> None:
    (tmp_path / "reference.txt").write_text(
        """
3.1.10

error-down auto-recovery

Синтаксис

• error-down auto-recovery cause link-flap interval interval

• no error-down auto-recovery cause link-flap

Параметры

interval Интервал восстановления
""",
        encoding="utf-8",
    )

    assert set(scan_documentation(tmp_path)) == {
        "error-down auto-recovery cause link-flap interval interval",
        "no error-down auto-recovery cause link-flap",
    }


def test_reads_syntax_bulleted_with_the_bullet_operator(tmp_path: Path) -> None:
    """A manual that began life as a PDF marks syntax with U+2219, not U+2022.

    The two look identical on screen and are different code points, so a
    reference converted out of a PDF hid every one of its syntax lines behind a
    bullet the parser did not recognise.
    """
    (tmp_path / "reference.txt").write_text(
        """
10.4.22

ddm report interval

Синтаксис

∙ ddm report interval value

∙ no ddm report interval

Параметры

value Интервал отчёта
""",
        encoding="utf-8",
    )

    assert set(scan_documentation(tmp_path)) == {
        "ddm report interval value",
        "no ddm report interval",
    }


def test_a_docx_manual_is_read_as_cards(tmp_path: Path, monkeypatch) -> None:
    """Three real pages of the Centec reference, read end to end by the scan.

    The excerpt carries three cards and a profile asks for twenty, so the
    threshold is lowered rather than the fixture inflated: what is under test
    is the wiring - that a .docx is opened at all, that the profile chosen to
    read it is the one used to split it, and that the commands come out - not
    the threshold, which has its own test.
    """
    import dataclasses

    from cliradar import docs
    from cliradar.docparse.profile import builtin

    excerpt = Path(__file__).parent / "docparse" / "fixtures" / "centec_pages.docx"
    small = dataclasses.replace(builtin("centec_eng"), min_cards=3)
    monkeypatch.setattr(docs, "available", lambda: [small])

    commands = scan_documentation(excerpt)

    assert "configure" in commands
    assert "debug cli cmd" in commands  # expanded out of { cmd | pty | ... }
    # The eight printed lines of one form are one command, so its choices
    # expand into the views it actually offers - and the domain the table
    # states for level-value is the placeholder the device's help prints.
    assert "command-privilege level <0-15> view bgp-af-ipv4-mcast" in commands
    # The line broke inside that name, and the hyphen it broke on belongs to
    # the name. Closed up without it, the command would be a phantom.
    assert not any("bgp-af-ipv4mcast" in name for name in commands)
    # Nor did any continuation line become a command of its own.
    assert not any(name.startswith("mcast |") for name in commands)


def test_a_docx_no_profile_earns_yields_nothing(tmp_path: Path) -> None:
    # The same excerpt at the real threshold: three cards is not a manual, and
    # there is no line reader to fall back to when the file is a zip.
    excerpt = Path(__file__).parent / "docparse" / "fixtures" / "centec_pages.docx"

    assert scan_documentation(excerpt) == {}


def test_a_file_that_is_not_really_a_docx_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "broken.docx").write_bytes(b"not a zip at all")

    assert scan_documentation(tmp_path) == {}


def test_reports_a_manual_that_yielded_nothing(tmp_path: Path) -> None:
    # A .docx no profile can read is otherwise indistinguishable from one that
    # was read: the run prints a command count either way. See defect 1 in
    # docs/DOCPARSE_DEFECTS_RU.md.
    (tmp_path / "readable.md").write_text("```\nshow version\n```\n", encoding="utf-8")
    (tmp_path / "manual.docx").write_bytes(b"not a zip at all")
    skipped: list[tuple[Path, str]] = []

    commands = scan_documentation(tmp_path, on_skip=lambda p, why: skipped.append((p, why)))

    assert set(commands) == {"show version"}
    assert [p.name for p, _ in skipped] == ["manual.docx"]
    assert "архив" in skipped[0][1]


def test_reports_a_file_read_without_finding_a_command(tmp_path: Path) -> None:
    (tmp_path / "prose.md").write_text("Просто абзац текста без команд.\n", encoding="utf-8")
    skipped: list[tuple[Path, str]] = []

    scan_documentation(tmp_path, on_skip=lambda p, why: skipped.append((p, why)))

    assert [p.name for p, _ in skipped] == ["prose.md"]
    assert "не дал ни одной команды" in skipped[0][1]


def test_skip_callback_is_optional(tmp_path: Path) -> None:
    (tmp_path / "manual.docx").write_bytes(b"not a zip at all")
    # No callback must not raise.
    assert scan_documentation(tmp_path) == {}
