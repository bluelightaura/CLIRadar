from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 - operator's own manual, not network input
import zipfile
from pathlib import Path

from .docparse import docx, is_card_reference, mark_parameters, split_cards
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

FENCE_RE = re.compile(r"^\s*```")
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
) -> tuple[str, str] | None:
    table_match = TABLE_RE.match(raw_line)
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
) -> None:
    entry = commands.setdefault(command, CommandEntry(command=command))
    entry.source.add(f"documentation:{path.as_posix()}")
    if description and not entry.description:
        entry.description = description


def _read_docx(path: Path) -> tuple[str, Profile] | None:
    """A .docx manual as text, with the profile it took to read it.

    Unlike a text file, this one cannot be read before a profile is chosen:
    telling a block title from a line of prose in a paged conversion needs the
    list of block names up front. So the profiles carrying such a list are
    tried in turn and the first the document earns wins; one that carries none
    cannot read a .docx and is not offered the chance. Nothing is returned for
    a document no profile earns - there is no line reader to fall back to when
    the file is a zip.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            packed = sum(item.file_size for item in archive.infolist())
    except (zipfile.BadZipFile, OSError):
        return None
    if packed > MAX_PACKED_DOCUMENT_BYTES:
        return None
    for profile in available():
        if not profile.sections:
            continue
        try:
            text = docx.read(path, profile)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
            return None
        if len(text.encode("utf-8", "ignore")) > MAX_DOCUMENT_BYTES:
            return None
        if is_card_reference(split_cards(text, profile), profile):
            return text, profile
    return None


def scan_documentation(root: Path, on_progress=None) -> dict[str, CommandEntry]:
    commands: dict[str, CommandEntry] = {}
    if not root.exists():
        return commands

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
            read = _read_docx(path)
            if read is None:
                continue
            text, docx_profile = read
        else:
            text, docx_profile = path.read_text(encoding="utf-8", errors="replace"), None
        if path.suffix.lower() == ".txt" and COMPACT_REFERENCE_MARKER_RE.search(text):
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
            for card in cards:
                for syntax in mark_parameters(card, profile):
                    for command in _expand_syntax(syntax):
                        _add_command(commands, command, "", path)
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
            continue

        in_fence = False
        for raw_line in text.splitlines():
            if FENCE_RE.match(raw_line):
                in_fence = not in_fence
                continue

            candidate = _line_candidate(
                raw_line,
                in_fence=in_fence,
                plain_text=path.suffix.lower() == ".txt",
            )
            if not candidate:
                continue
            for command in _expand_syntax(candidate[0]):
                _add_command(commands, command, candidate[1], path)
    if callable(on_progress) and total:
        on_progress(total, total, "")  # a final full tick closes the bar
    return commands
