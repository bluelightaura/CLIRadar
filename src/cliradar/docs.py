from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 - operator's own manual, not network input
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .docparse import (
    docx,
    is_card_reference,
    mark_parameters,
    purpose_for,
    repair_welded_tokens,
    split_cards,
)
from .docparse.profile import Profile, available
from .models import CommandEntry

SUPPORTED_SUFFIXES = {".md", ".txt", ".rst", ".docx"}
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
# A .docx is a zip, so its size on disk says little about the work reading it
# costs: the manual this reader was written for is 5 MB packed and 113 MB of
# XML unpacked. The archive is checked against what it claims to hold before
# any of it is parsed, and the text it yields is then held to the same limit
# every other document is.
MAX_PACKED_DOCUMENT_BYTES = 256 * 1024 * 1024
MAX_EXPANSIONS = 512

FENCE_RE = re.compile(r"^\s*```(?P<info>[A-Za-z0-9_+-]*)")
# Languages a fence declares that are certainly not a device's command line.
# Markdown lets a block say what it holds, and a manual's own listings say
# nothing or "text" while a project's README says "bash" over the shell it
# wants run. Reading those as device commands put "pytest", "pip-audit" and
# "export SWITCH_PASSWORD='...'" into the catalog, where a compare reports each
# of them as a command the switch is missing.
NOT_A_COMMAND_LINE = frozenset(
    {
        "bash", "sh", "shell", "zsh", "fish", "console", "shell-session",
        "python", "py", "python3", "ruby", "perl", "go", "rust", "c", "cpp",
        "java", "js", "javascript", "ts", "typescript",
        "yaml", "yml", "json", "toml", "ini", "xml", "html", "css",
        "diff", "patch", "sql", "make", "makefile", "dockerfile",
    }
)
COMMAND_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\s+\S+)*$")
PROMPT_COMMAND_RE = re.compile(
    r"^\s*[\w()./@:-]+[>#]\s*(?P<command>[a-z][^\r\n]*)\s*$"
)
LABELED_COMMAND_RE = re.compile(
    r"^\s*(?:command|syntax|command\s+format|usage|example|"
    r"команда|синтаксис|формат\s*команды|пример)\s*:\s*"
    r"(?P<command>[a-z][^\r\n]*)\s*$",
    re.IGNORECASE,
)
TABLE_RE = re.compile(
    r"^\s*\|\s*`?(?P<command>[a-z][^|`]+)`?\s*\|\s*(?P<description>[^|]+)",
    re.IGNORECASE,
)
# A markdown table is read as a table of commands only when its own header says
# that is what it is. Every table has pipes in it, and reading them all put the
# two cells of a comparison table - "| `en` | 5 | 7063 |" - into the catalog as
# the commands "en" and "ru". A manual naming its column is not a guess.
# The "|---|---|" rule under a markdown table's header, which is not a row.
_TABLE_RULE_RE = re.compile(r"^\s*\|[\s:|-]+$")
TABLE_HEADER_RE = re.compile(
    r"^\s*\|\s*`?\s*(?:command|syntax|cli|команда|синтаксис)\b", re.IGNORECASE
)
COMMAND_DESCRIPTION_RE = re.compile(
    r"^\s*(?P<command>[a-z][^\t]*?)(?:\t+|\s{2,})(?P<description>\S.*?)\s*$"
)
FORMAT_HEADING_RE = re.compile(
    r"^\s*(?:command\s+format|command\s+form|command\s+syntax|syntax|"
    r"формат\s+команды|синтаксис)\s*:?\s*$",
    re.IGNORECASE,
)
REFERENCE_MARKER_RE = re.compile(
    r"^\s*(?:command\s+format|command\s+form|command\s+syntax|syntax|"
    r"формат\s*команды|форматкоманды|синтаксис)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COMPACT_REFERENCE_MARKER_RE = re.compile(
    r"^\s*форматкоманды\s*$",
    re.IGNORECASE | re.MULTILINE,
)
END_HEADING_RE = re.compile(
    r"^\s*(?:parameter|parameter\s+description|command\s+function|command\s+view|"
    r"default(?:\s+value)?|usage\s+example|command\s+guidance|description|note|"
    r"параметр(?:ы|ов)?|описание|функция|режим|значение|пример|примечание)\b",
    re.IGNORECASE,
)
TOC_LINE_RE = re.compile(
    r"^\s*\d+(?:\.\d+)+\s+(?P<command>.+?)\s*(?:\.\s*){3,}\d+\s*$"
)
SECTION_COMMAND_RE = re.compile(
    r"^\s*\d+(?:\.\d+)+\s+(?P<command>[a-z][^\r\n.]*)\s*$"
)
SECTION_NUMBER_RE = re.compile(r"^\s*\d+(?:\.\d+)+\s*$")
# Bullets a converted manual can start a syntax line with. The list is wider
# than it looks: U+2219 BULLET OPERATOR and U+00B7 MIDDLE DOT are what a PDF
# re-saved as .docx emits, and they are different code points from the
# typographic U+2022 BULLET a hand-written document uses.
SYNTAX_BULLET_RE = re.compile(
    r"^\s*(?:[•●▪◦∙·‣▫○▸➢]\s*|[-*]\s+)"
)
TRAILING_COMMAND_HEADING_RE = re.compile(
    r"\s+(?:command|команда)\s*$",
    re.IGNORECASE,
)
STRUCTURED_NOISE_RE = re.compile(
    r"^(?:chapter\b|switch\s+command\s+line\s+manual\b|table\s+of\b|"
    r"содержание\b|руководство\b)",
    re.IGNORECASE,
)
IGNORED_PREFIXES = ("sudo ", "ssh ", "telnet ")
# An arrow says the line is showing something turning into something else - a
# pipeline in a diagram, a before-and-after, the output a ping printed. None of
# them is a command an operator types, and read as one they arrive in the
# catalog whole: "ping 172.16.1.101 -> 3 packets transmitted, 3 received".
ARROW_RE = re.compile(r"(?:->|=>|\u2192|\u21d2)")


def _normalize_command(value: str) -> str | None:
    command = " ".join(value.strip().strip("`").split())
    if (
        not command
        or len(command.encode("utf-8")) > 512
        or not command.isascii()
        or not command.isprintable()
        or "?" in command
        or command.endswith((".", ",", ":", ";"))
        or command.startswith(IGNORED_PREFIXES)
        or not COMMAND_RE.fullmatch(command)
        or ARROW_RE.search(command)
        or len(command.split()) > 32
    ):
        return None
    return command


def _balanced(value: str) -> bool:
    return (
        value.count("{") == value.count("}")
        and value.count("[") == value.count("]")
    )


def _continues(value: str) -> bool:
    return value.rstrip().endswith(("|", "-", "\\"))


def _looks_like_syntax(value: str) -> bool:
    line = value.strip()
    return bool(
        line
        and line.isascii()
        and line.isprintable()
        and re.match(r"^[a-z]", line)
        and "#" not in line
        and not STRUCTURED_NOISE_RE.match(line)
        and not line.endswith((".", ",", ":", ";"))
    )


def _is_end_heading(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped and stripped[0].isupper() and END_HEADING_RE.match(stripped))


def _syntax_root(value: str) -> str:
    tokens = value.split()
    index = 1 if tokens and tokens[0].casefold() in {"no", "undo", "default"} else 0
    return tokens[index].casefold() if len(tokens) > index else ""


def _strip_syntax_marker(value: str) -> str:
    return SYNTAX_BULLET_RE.sub("", value).strip()


def _section_root_at(lines: list[str], index: int) -> str:
    match = SECTION_COMMAND_RE.match(lines[index])
    if match:
        return _syntax_root(match.group("command"))
    if not SECTION_NUMBER_RE.match(lines[index]):
        return ""

    for candidate in lines[index + 1 : index + 5]:
        stripped = _strip_syntax_marker(candidate)
        if not stripped:
            continue
        stripped = TRAILING_COMMAND_HEADING_RE.sub("", stripped)
        return _syntax_root(stripped) if _looks_like_syntax(stripped) else ""
    return ""


def _extract_structured_syntax(text: str) -> list[str]:
    lines = text.splitlines()
    commands: list[str] = []
    index = 0
    section_root = ""

    while index < len(lines):
        detected_root = _section_root_at(lines, index)
        if detected_root:
            section_root = detected_root
        if not FORMAT_HEADING_RE.match(lines[index]):
            index += 1
            continue

        index += 1
        buffer = ""
        while index < len(lines):
            raw_line = lines[index]
            stripped = _strip_syntax_marker(raw_line)
            if FORMAT_HEADING_RE.match(raw_line) or _is_end_heading(raw_line):
                break
            section_match = SECTION_COMMAND_RE.match(raw_line)
            if section_match:
                section_root = _syntax_root(section_match.group("command"))
                index += 1
                continue
            index += 1

            if not stripped or STRUCTURED_NOISE_RE.match(stripped):
                continue
            if not buffer:
                if not _looks_like_syntax(stripped):
                    continue
                buffer = stripped
            else:
                buffer = f"{buffer}{stripped}" if buffer.endswith("-") else f"{buffer} {stripped}"

            if _balanced(buffer) and not _continues(buffer):
                candidate_root = _syntax_root(buffer)
                if _looks_like_syntax(buffer) and (
                    not section_root or candidate_root == section_root
                ):
                    commands.append(buffer)
                buffer = ""

        if buffer and _balanced(buffer) and _looks_like_syntax(buffer):
            candidate_root = _syntax_root(buffer)
            if not section_root or candidate_root == section_root:
                commands.append(buffer)

    return commands


def _extract_toc_syntax(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        match = TOC_LINE_RE.match(line)
        if not match:
            continue
        command = re.sub(r"\s*\([^)]*\)\s*", " ", match.group("command")).strip()
        if _looks_like_syntax(command):
            commands.append(command)
    return commands


def _grammar_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    buffer: list[str] = []
    stack: list[str] = []
    pairs = {"}": "{", "]": "["}

    for char in value:
        if char in "{[":
            stack.append(char)
        elif char in "}]" and stack and stack[-1] == pairs[char]:
            stack.pop()
        if char.isspace() and not stack:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def _split_alternatives(value: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    stack: list[str] = []
    pairs = {"}": "{", "]": "["}

    for char in value:
        if char in "{[":
            stack.append(char)
        elif char in "}]" and stack and stack[-1] == pairs[char]:
            stack.pop()
        if char == "|" and not stack:
            parts.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(char)
    parts.append("".join(buffer).strip())
    return [part for part in parts if part]


def _expand_expression(value: str, cap: int = MAX_EXPANSIONS) -> list[list[str]]:
    paths: list[list[str]] = [[]]
    for token in _grammar_tokens(value):
        variants: list[list[str]]
        if token.startswith("{") and token.endswith("}"):
            alternatives = _split_alternatives(token[1:-1])
            variants = [
                path
                for alternative in alternatives
                for path in _expand_expression(alternative, cap)
            ]
        elif token.startswith("[") and token.endswith("]"):
            alternatives = _split_alternatives(token[1:-1])
            variants = [[]] + [
                path
                for alternative in alternatives
                for path in _expand_expression(alternative, cap)
            ]
        elif "|" in token and token != "|":  # nosec B105 - grammar token, not a credential
            variants = [[alternative] for alternative in token.split("|") if alternative]
        else:
            variants = [[token]]

        expanded = [prefix + variant for prefix in paths for variant in variants]
        if len(expanded) > cap:
            return [_grammar_tokens(value)]
        paths = expanded
    return paths


def _expand_syntax(value: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", value).strip()
    commands: list[str] = []
    for path in _expand_expression(normalized):
        command = _normalize_command(" ".join(path))
        if command and not any(character in command for character in "{}[]"):
            commands.append(command)
    return commands


def _line_candidate(
    raw_line: str,
    *,
    in_fence: bool,
    plain_text: bool,
    in_command_table: bool = True,
) -> tuple[str, str] | None:
    table_match = TABLE_RE.match(raw_line) if in_command_table else None
    if table_match:
        return table_match.group("command"), table_match.group("description").strip()

    prompt_match = PROMPT_COMMAND_RE.match(raw_line)
    if prompt_match:
        return prompt_match.group("command"), ""

    labeled_match = LABELED_COMMAND_RE.match(raw_line)
    if labeled_match:
        return labeled_match.group("command"), ""

    if not (in_fence or plain_text):
        return None

    description_match = COMMAND_DESCRIPTION_RE.match(raw_line)
    if description_match:
        return (
            description_match.group("command"),
            description_match.group("description").strip(),
        )
    return raw_line, ""


def _add_command(
    commands: dict[str, CommandEntry],
    command: str,
    description: str,
    path: Path,
    language: str = "",
    prefer: str = "",
    spoken: dict[str, str] | None = None,
) -> None:
    """Record one command, and keep the best description offered for it.

    Two manuals of the same device describe the same command, so which text an
    entry ends up with was decided by which file was read first - and read
    first means earlier in the alphabet, which is not a reason. Of 7068
    commands described by both manuals only 5 kept the Russian sentence.

    A description already in the language the operator reads is never replaced.
    One in another language gives way to it, and everything else keeps the
    first text offered, as before.
    """
    entry = commands.setdefault(command, CommandEntry(command=command))
    entry.source.add(f"documentation:{path.as_posix()}")
    if not description:
        return
    held = (spoken or {}).get(command, "")
    if not entry.description or (prefer and language == prefer and held != prefer):
        entry.description = description
        if spoken is not None:
            spoken[command] = language


@dataclass
class _Skipped:
    """Files a documentation run read nothing out of, and why.

    A run reports how many commands it wrote and nothing about the manuals it
    passed over, so a folder of two manuals where only one was read looks
    exactly like a folder of two manuals where both were. This collects the
    refusals so the caller can say which file gave nothing.
    """

    entries: list[tuple[Path, str]] = field(default_factory=list)

    def add(self, path: Path, reason: str) -> None:
        self.entries.append((path, reason))

    def __bool__(self) -> bool:
        return bool(self.entries)


def _refuse(report: _Skipped | None, path: Path, reason: str) -> None:
    """Record why a document gave nothing.

    Returns None so a caller can write ``return _refuse(...)`` and say the
    refusal and its reason in one line.
    """
    if report is not None:
        report.add(path, reason)


def _read_docx(path: Path, report: _Skipped | None = None) -> tuple[str, Profile] | None:
    """A .docx manual as text, with the profile it took to read it.

    Unlike a text file, this one cannot be read before a profile is chosen:
    telling a block title from a line of prose in a paged conversion needs the
    list of block names up front. So the profiles carrying such a list are
    tried in turn and the first the document earns wins; one that carries none
    cannot read a .docx and is not offered the chance. Nothing is returned for
    a document no profile earns - there is no line reader to fall back to when
    the file is a zip.

    Every way of returning nothing says why into ``report``. A manual this
    reader declines is otherwise indistinguishable from one it read: the run
    prints a command count either way, and a count of 11986 looks like success
    even when one of the two manuals in the folder contributed none of it.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            packed = sum(item.file_size for item in archive.infolist())
    except (zipfile.BadZipFile, OSError):
        return _refuse(report, path, "не читается как .docx (повреждённый архив)")
    if packed > MAX_PACKED_DOCUMENT_BYTES:
        return _refuse(report, path, f"распакованный размер {packed} Б превышает предел")
    offered = False
    for profile in available():
        if not profile.sections:
            continue
        offered = True
        try:
            text = docx.read(path, profile)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
            return _refuse(report, path, "XML документа не разбирается")
        if len(text.encode("utf-8", "ignore")) > MAX_DOCUMENT_BYTES:
            return _refuse(report, path, "текст документа превышает предел размера")
        if is_card_reference(split_cards(text, profile), profile):
            return text, profile
    return _refuse(
        report,
        path,
        "ни один профиль не опознал документ как справочник карточек"
        if offered
        else "нет ни одного профиля, умеющего читать .docx",
    )


def scan_documentation(
    root: Path, on_progress=None, on_skip=None, prefer_language: str = ""
) -> dict[str, CommandEntry]:
    """Every command the documentation under ``root`` describes.

    ``on_skip`` is handed ``(path, reason)`` for each file that yielded no
    command, whether this reader declined it or read it and found nothing. A
    run is otherwise silent about the difference, and silence there reads as
    success - see docs/DOCPARSE_DEFECTS_RU.md, defect 1.

    ``prefer_language`` is the two-letter code the operator reads. Where two
    manuals describe one command, the description in that language wins over
    the one that merely came from an earlier filename - see ``_add_command``.
    """
    commands: dict[str, CommandEntry] = {}
    if not root.exists():
        return commands
    skipped = _Skipped()
    # Which language each held description is written in, so a later manual can
    # tell whether it has anything better to offer. Kept beside the catalog
    # rather than in it: it decides a description, it is not part of one.
    spoken: dict[str, str] = {}

    candidates = [root] if root.is_file() else root.rglob("*")
    paths = sorted(
        item
        for item in candidates
        if item.is_file()
        and not item.is_symlink()
        and item.suffix.lower() in SUPPORTED_SUFFIXES
        and (
            item.suffix.lower() == ".docx"
            or item.stat().st_size <= MAX_DOCUMENT_BYTES
        )
    )
    total = len(paths)
    for index, path in enumerate(paths):
        # Reading a folder of manuals is otherwise silent; a per-file tick lets
        # the caller draw "reading file k/N" so a docs run visibly progresses.
        if callable(on_progress):
            on_progress(index, total, path.name)
        if path.suffix.lower() == ".docx":
            read = _read_docx(path, skipped)
            if read is None:
                continue
            text, docx_profile = read
        else:
            text, docx_profile = path.read_text(encoding="utf-8", errors="replace"), None
        if path.suffix.lower() == ".txt" and COMPACT_REFERENCE_MARKER_RE.search(text):
            skipped.add(path, "сжатый справочник без пробелов - разбору не поддаётся")
            continue
        # A manual written as one card per command is read as that structure.
        # The line reader below cannot tell a syntax listing from the parameter
        # table beside it or the worked example beneath it, and on a document of
        # this shape that difference is most of the catalog.
        # Each vendor names the blocks differently; the reading is the same.
        # The profiles are tried in turn and the first the document earns is
        # used - see cliradar.docparse.profile.
        read_as_cards = False
        for profile in [docx_profile] if docx_profile else available():
            cards = split_cards(text, profile)
            if not is_card_reference(cards, profile):
                continue
            # The conversion welds two words into one often enough to matter,
            # and only the document as a whole can say which - see
            # repair_welded_tokens. Done before marking so the table rows the
            # weld hid are found.
            repair_welded_tokens(cards)
            for card in cards:
                for syntax in mark_parameters(card, profile):
                    for command in _expand_syntax(syntax):
                        # The card's purpose block, picked for this form of the
                        # command. Passing "" here is what left every entry of
                        # the catalog without a description - defect 4 in
                        # docs/DOCPARSE_DEFECTS_RU.md.
                        _add_command(
                            commands,
                            command,
                            purpose_for(card, command),
                            path,
                            language=profile.language,
                            prefer=prefer_language,
                            spoken=spoken,
                        )
            read_as_cards = True
            break
        if read_as_cards:
            continue
        structured = _extract_structured_syntax(text) if path.suffix.lower() == ".txt" else []
        if structured:
            for syntax in structured + _extract_toc_syntax(text):
                for command in _expand_syntax(syntax):
                    _add_command(commands, command, "", path)
            continue
        if path.suffix.lower() == ".txt" and REFERENCE_MARKER_RE.search(text):
            # A converted reference with destroyed spacing (for example
            # "Форматкоманды") cannot be parsed reliably as a plain command list.
            skipped.add(path, "конверсия с уничтоженными пробелами")
            continue

        in_fence = False
        # Whether the markdown table now being read said it holds commands.
        # True until a table says otherwise, so a document with no tables and a
        # plain list of commands behaves exactly as before.
        in_command_table = True
        seen_a_table_row = False
        # A fence that named a language other than the device's own is skipped
        # whole: its contents are shell, config or code that happens to look
        # like a command line. See NOT_A_COMMAND_LINE.
        foreign_fence = False
        for raw_line in text.splitlines():
            fence = FENCE_RE.match(raw_line)
            if fence:
                if in_fence:
                    in_fence, foreign_fence = False, False
                else:
                    in_fence = True
                    foreign_fence = fence.group("info").lower() in NOT_A_COMMAND_LINE
                continue
            if foreign_fence:
                continue
            # Which table is being read, tracked outside the fence logic: a
            # table's header names its columns once and the rows below inherit
            # that. A row standing under no header at all is read as one, which
            # is how a plain command list keeps working.
            if not in_fence and raw_line.lstrip().startswith("|"):
                if TABLE_HEADER_RE.match(raw_line):
                    in_command_table = True
                elif not _TABLE_RULE_RE.match(raw_line) and not seen_a_table_row:
                    in_command_table = False
                seen_a_table_row = True
            elif not raw_line.strip():
                in_command_table, seen_a_table_row = True, False

            candidate = _line_candidate(
                raw_line,
                in_fence=in_fence,
                plain_text=path.suffix.lower() == ".txt",
                in_command_table=in_command_table,
            )
            if not candidate:
                continue
            for command in _expand_syntax(candidate[0]):
                _add_command(commands, command, candidate[1], path)
    for path in paths:
        marker = f"documentation:{path.as_posix()}"
        if any(marker in entry.source for entry in commands.values()):
            continue
        if not any(seen == path for seen, _ in skipped.entries):
            skipped.add(path, "прочитан, но не дал ни одной команды")
    if callable(on_skip):
        for path, reason in skipped.entries:
            on_skip(path, reason)
    if callable(on_progress) and total:
        on_progress(total, total, "")  # a final full tick closes the bar
    return commands
