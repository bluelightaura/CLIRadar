"""Reading the device's running configuration and matching it to the catalog.

The crawler answers "what can this box be told to do"; this module answers
"what has it actually been told". The two halves check each other, and the
interesting result is the disagreement:

    a configured line the catalog does not contain is a hole in the scan -
    the device is executing a command the crawl never found,

and that is the only evidence available that a command surface is incomplete
without a second device to compare against.

Nothing here executes anything: the configuration is read with one command the
operator names, and everything after that is text processing. The parse is
deliberately structural rather than vendor-specific - indentation and section
separators are all that is needed to rebuild the hierarchy, so a platform this
code has never seen still yields a tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Catalog
from .parser import clean_terminal_output, option_kind

# A line that only separates sections. VRP writes "#", IOS-like platforms "!".
SEPARATOR_RE = re.compile(r"^[#!]+\s*$")
# A trailing marker that ends the dump rather than configuring anything.
TERMINATOR_RE = re.compile(r"^(?:return|end|exit|quit)\s*$", re.IGNORECASE)
COMMENT_RE = re.compile(r"^\s*(?:!|;|//)")
PROMPT_RE = re.compile(r"^\S*[>#$]\s*$")
DIGITS_RE = re.compile(r"\d+")
# Tokens that introduce a secret value. The value's position is vendor-specific
# - it can sit one or several tokens after the keyword, and before or after
# other qualifiers ("community read <secret>" vs "community <secret> ro") - so
# redaction keeps the line up to and including the keyword and blanks the whole
# remainder rather than guessing which token is the secret. Matching is on whole
# tokens, so a compound like "hmac-sha-256" is not mistaken for a secret.
SECRET_KEYWORDS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "community",
        "pre-shared",
        "psk",
        "key",
        "key-string",
        "authentication-key",
        "shared-key",
        "private-key",
        "certificate",
        "cipher",
    }
)
# Free text that follows a keyword and is not part of the command grammar.
VERBATIM_START_RE = re.compile(
    r"^(?:banner|header)\b.*?(?P<delimiter>[\"'^%~])(?P<rest>.*)$", re.IGNORECASE
)


@dataclass(frozen=True)
class ConfigLine:
    """One configured line together with the views it sits inside."""

    text: str
    number: int
    indent: int
    # The line and every view above it, outermost first. This is the join key
    # against the catalog: a catalog command is a path from the CLI root, and
    # so is this.
    path: tuple[str, ...]
    verbatim: bool = False

    @property
    def depth(self) -> int:
        return len(self.path) - 1

    @property
    def skeleton(self) -> str:
        """The line with its values folded away.

        What belongs in a shared report is which command was configured, never
        with what: an unmatched line is evidence about the scan, and its
        address, key or interface number is about the customer. A secret value
        is blanked outright first, so an unexplained secret line names its
        command in the gap list without carrying the secret into it.
        """
        return skeleton_of(_blank_secret_line(self.text))


def skeleton_of(text: str) -> str:
    tokens = []
    for token in text.split():
        # A word carrying a digit is an address, a number or an identifier; a
        # very long word is a key or a hash. Keywords are neither.
        if any(char.isdigit() for char in token) or len(token) > 24:
            tokens.append("<value>")
        else:
            tokens.append(token)
    return " ".join(tokens)


def _blank_secret_line(line: str) -> str:
    """Keep a line up to its first secret keyword and blank the remainder.

    The secret value can sit anywhere after the keyword and its position differs
    between platforms, so nothing after the keyword is trusted to be safe. A
    keyword that is the last token introduces no value and leaves the line as it
    is, which keeps qualifier-only lines ("authentication-mode md5") intact.
    """
    indent = line[: len(line) - len(line.lstrip())]
    tokens = line.split()
    for index, token in enumerate(tokens):
        if token.lower() in SECRET_KEYWORDS and index + 1 < len(tokens):
            return f"{indent}{' '.join(tokens[: index + 1])} <redacted>"
    return line


def redact_secrets(text: str) -> str:
    """Blank the value of every line that names a secret, line by line."""
    return "\n".join(_blank_secret_line(line) for line in text.splitlines())


def _strip_echo(lines: list[str], command: str) -> list[str]:
    """Drop the echoed command and the prompt the device redraws afterwards."""
    typed = command.strip()
    start = 0
    for index, line in enumerate(lines[:5]):
        stripped = line.strip()
        if not stripped or (typed and stripped.endswith(typed)) or PROMPT_RE.match(stripped):
            start = index + 1
        else:
            break
    end = len(lines)
    while end > start:
        stripped = lines[end - 1].strip()
        if not stripped or PROMPT_RE.match(stripped) or TERMINATOR_RE.match(stripped):
            end -= 1
        else:
            break
    return lines[start:end]


def parse_config(output: str, command: str = "") -> list[ConfigLine]:
    """Rebuild the view hierarchy of a configuration dump.

    Indentation carries the hierarchy on every platform that has one, and a
    separator line ("#" on VRP, "!" elsewhere) closes whatever view was open.
    Both rules are structural, so a platform whose keywords are unknown still
    parses; a platform with neither yields a flat list, which is still correct.

    Free text - a login banner, a certificate - is kept as one opaque line.
    Its body is prose, and letting it into the hierarchy would invent views out
    of an indented sentence.
    """
    lines = _strip_echo(clean_terminal_output(output).splitlines(), command)
    parsed: list[ConfigLine] = []
    stack: list[tuple[int, str]] = []
    pending_delimiter: str | None = None
    verbatim_owner: int | None = None

    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if pending_delimiter is not None:
            # Inside free text: nothing is a command until the delimiter closes.
            if pending_delimiter in line:
                pending_delimiter = None
                verbatim_owner = None
            continue
        if not line.strip():
            continue
        if SEPARATOR_RE.match(line.strip()):
            # A separator ends the section it follows, whatever its depth.
            stack.clear()
            continue
        if COMMENT_RE.match(line) or TERMINATOR_RE.match(line.strip()):
            continue

        indent = len(line) - len(line.lstrip())
        text = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        path = tuple(item[1] for item in stack) + (text,)

        opening = VERBATIM_START_RE.match(text)
        verbatim = False
        if opening and opening.group("delimiter") not in opening.group("rest"):
            pending_delimiter = opening.group("delimiter")
            verbatim_owner = number
            verbatim = True
        parsed.append(
            ConfigLine(
                text=text,
                number=number,
                indent=indent,
                path=path,
                verbatim=verbatim,
            )
        )
        if not verbatim:
            stack.append((indent, text))
    if verbatim_owner is not None:
        # An unterminated banner would otherwise swallow the rest of the dump;
        # the lines after it were already skipped, so say so rather than
        # pretending the configuration ended there.
        parsed.append(
            ConfigLine(
                text="<unterminated free text>",
                number=verbatim_owner,
                indent=0,
                path=("<unterminated free text>",),
                verbatim=True,
            )
        )
    return parsed


class CommandTrie:
    """The catalog, arranged so a configured line can be looked up in it.

    A catalog command is a sequence of tokens where some stand for a value
    (`<1-4094>`, `IFNAME`); those match any word. Concrete instance numbers are
    folded as well, so a catalog that was walked through `interface 10ge1/0/1`
    still recognises `interface 10GE1/0/24` - the same command on another port.
    """

    WILDCARD = "\x00*"

    def __init__(self) -> None:
        self._root: dict[str, Any] = {}
        self.size = 0

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> CommandTrie:
        trie = cls()
        for command, entry in catalog.commands.items():
            if entry.on_device:
                trie.add(command)
        return trie

    def add(self, command: str) -> None:
        node = self._root
        for token in command.split():
            key = self.WILDCARD if option_kind(token) == "parameter" else token.lower()
            node = node.setdefault(key, {})
        node["\x00"] = command
        self.size += 1

    def match(self, tokens: tuple[str, ...]) -> str | None:
        """The catalog command this token sequence is an instance of, if any.

        A literal keyword is preferred over a wildcard, and a folded instance
        number is the last resort, so `vlan 10` matches `vlan <1-4094>` rather
        than some other node that happens to accept any word.
        """
        if not tokens:
            return None
        return self._match(self._root, tokens, 0)

    def _match(self, node: dict[str, Any], tokens: tuple[str, ...], index: int) -> str | None:
        if index == len(tokens):
            found = node.get("\x00")
            return found if isinstance(found, str) else None
        token = tokens[index].lower()
        for key in self._candidates(node, token):
            child = node.get(key)
            if isinstance(child, dict):
                found = self._match(child, tokens, index + 1)
                if found:
                    return found
        return None

    def _candidates(self, node: dict[str, Any], token: str) -> list[str]:
        order = []
        if token in node:
            order.append(token)
        if self.WILDCARD in node:
            order.append(self.WILDCARD)
        folded = DIGITS_RE.sub("*", token)
        if folded != token:
            order.extend(
                key
                for key in node
                if key not in (token, self.WILDCARD, "\x00")
                and DIGITS_RE.sub("*", key) == folded
            )
        return order


@dataclass
class LineMatch:
    line: ConfigLine
    status: str  # "matched" | "matched_elsewhere" | "unmatched" | "free-text"
    command: str | None = None


@dataclass
class ConfigCoverage:
    """What the configuration proved about the catalog."""

    command: str
    lines: list[LineMatch] = field(default_factory=list)
    view_prefixes: tuple[tuple[str, ...], ...] = ()

    def count(self, status: str) -> int:
        return sum(item.status == status for item in self.lines)

    @property
    def unmatched(self) -> list[LineMatch]:
        return [item for item in self.lines if item.status == "unmatched"]

    def to_dict(self) -> dict[str, Any]:
        checked = sum(item.status != "free-text" for item in self.lines)
        matched = self.count("matched") + self.count("matched_elsewhere")
        gaps: dict[str, dict[str, Any]] = {}
        for item in self.unmatched:
            skeleton = " ".join(
                skeleton_of(view) for view in item.line.path[:-1]
            )
            key = f"{skeleton} {item.line.skeleton}".strip()
            record = gaps.setdefault(key, {"command": key, "occurrences": 0})
            record["occurrences"] += 1
        return {
            "source_command": self.command,
            "lines": len(self.lines),
            "matched": self.count("matched"),
            "matched_elsewhere": self.count("matched_elsewhere"),
            "unmatched": self.count("unmatched"),
            "free_text": self.count("free-text"),
            # The share of configured lines the crawl can account for. A number
            # below 1 is the scan's own incompleteness, measured against the
            # only witness that cannot be argued with: the running device.
            "coverage": round(matched / checked, 4) if checked else 1.0,
            # Values are folded away: which command is missing belongs in a
            # shared report, the address it was configured with does not.
            "missing_from_catalog": sorted(
                gaps.values(), key=lambda item: (-int(item["occurrences"]), str(item["command"]))
            ),
        }


def correlate(
    lines: list[ConfigLine],
    catalog: Catalog,
    *,
    command: str = "",
    view_prefixes: tuple[tuple[str, ...], ...] = (),
) -> ConfigCoverage:
    """Decide, for each configured line, whether the catalog knows it.

    A configured line is typed inside a view, and the catalog stores commands
    as paths from the CLI root - so the same command appears under whatever
    entry path the scan took to reach it ("system-view interface X ..."). The
    line is therefore tried against the catalog under each known entry path
    before it is declared missing, and a line whose own words are known but
    whose path is not is reported apart: that is a gap in the map of views, not
    in the map of commands.
    """
    trie = CommandTrie.from_catalog(catalog)
    prefixes: list[tuple[str, ...]] = [()]
    for prefix in view_prefixes:
        tokens = tuple(word for item in prefix for word in item.split())
        if tokens and tokens not in prefixes:
            prefixes.append(tokens)

    coverage = ConfigCoverage(command=command, view_prefixes=tuple(prefixes))
    for line in lines:
        if line.verbatim:
            coverage.lines.append(LineMatch(line, "free-text"))
            continue
        path_tokens = tuple(word for view in line.path for word in view.split())
        found = _first_match(trie, prefixes, path_tokens)
        if found:
            coverage.lines.append(LineMatch(line, "matched", found))
            continue
        # The command itself, without the views it was typed in.
        found = _first_match(trie, prefixes, tuple(line.text.split()))
        if found:
            coverage.lines.append(LineMatch(line, "matched_elsewhere", found))
            continue
        coverage.lines.append(LineMatch(line, "unmatched"))
    return coverage


def _first_match(
    trie: CommandTrie,
    prefixes: list[tuple[str, ...]],
    tokens: tuple[str, ...],
) -> str | None:
    for prefix in prefixes:
        found = trie.match(prefix + tokens)
        if found:
            return found
    return None


def render_config_yaml(lines: list[ConfigLine], coverage: ConfigCoverage) -> str:
    """The configuration as a tree, annotated with what the catalog knows.

    This is the artifact an engineer reads: the device's own configuration,
    nested the way the CLI nests it, with the lines the scan cannot explain
    marked in place rather than listed elsewhere.
    """
    import yaml

    status_by_number = {item.line.number: item.status for item in coverage.lines}
    tree: dict[str, Any] = {}
    nodes: dict[tuple[str, ...], dict[str, Any]] = {(): tree}
    for line in lines:
        parent = nodes.get(line.path[:-1], tree)
        label = line.text
        status = status_by_number.get(line.number, "unmatched")
        if status == "unmatched":
            label = f"{line.text}   # НЕ НАЙДЕНО В КАТАЛОГЕ"
        child: dict[str, Any] = {}
        # Two identical lines in one view are legal; keep both readable.
        if label in parent:
            label = f"{label} ({line.number})"
        parent[label] = child
        nodes[line.path] = child

    def compact(node: dict[str, Any]) -> dict[str, Any] | None:
        return {key: compact(value) for key, value in node.items()} or None

    summary = coverage.to_dict()
    header = (
        "# Конфигурация устройства, разобранная CLIRadar.\n"
        f"# Источник: `{coverage.command}`; строк: {summary['lines']};"
        f" покрытие каталогом: {summary['coverage']:.1%}.\n"
        f"# Не найдено в каталоге: {summary['unmatched']}"
        " (помечены в дереве комментарием).\n"
        "# Значения паролей и ключей вычищены; остальное - как на устройстве,\n"
        "# поэтому файл приватный и не предназначен для публикации.\n"
    )
    body = yaml.dump(
        compact(tree) or {},
        Dumper=_dumper(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    return header + body


def _dumper() -> type:
    from .export import _CompactDumper

    return _CompactDumper
