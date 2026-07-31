from __future__ import annotations

import re
from dataclasses import dataclass

from .models import HelpOption


@dataclass(frozen=True)
class ParserProfile:
    """Vendor deviations from the strict reading of contextual help.

    Both defaults are the safe reading. Each one trades a class of missed
    commands for a class of invented ones, so which way a platform leans is an
    observation about that platform, not something to assume for all of them.
    Enable a flag only after seeing the behaviour in a lab.
    """

    # Accept a lone indented word as an option. Some CLIs list options with no
    # description at all; on others an indented one-word status or banner line
    # would become a command that does not exist.
    accept_undescribed_options: bool = False
    # Read `error`, `unknown`, `invalid` and friends as command names when they
    # appear inside the indented option block. True on platforms that really do
    # name commands that way (`debug bgp error`, `unknown-unicast`); false
    # everywhere else, where such a line is the device refusing the query.
    error_words_are_commands: bool = False


STRICT_PROFILE = ParserProfile()

ANSI_RE = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
BACKSPACE_RE = re.compile(r".\x08")
OPTION_RE = re.compile(
    r"^\s+(?P<token>\S+)(?:\s{2,}|\t+)(?P<description>\S.*?)\s*$"
)
# An option the device listed without a description. Restricted to something
# shaped like a CLI token so a wrapped description ending on a lone word is not
# mistaken for one; a trailing `.` or `,` is prose, not a command. A leading
# `<` is a placeholder, not prose: on the reference platform `<1-100>` appears
# alone on its line wherever a numeric parameter has no description.
TOKEN_ONLY_RE = re.compile(r"^\s+(?P<token>[A-Za-z0-9<][\w.:/<>\[\]{}|-]*)\s*$")
CR_ONLY_RE = re.compile(
    r"^\s+(?P<token><cr>|<return>|<enter>|<\[enter\]>|\[enter\])\s*$",
    re.IGNORECASE,
)
PROMPT_RE = re.compile(r"^(?!<)\S.*[>#]\s*$")
HEADER_RE = re.compile(
    r"^(?:possible\s+completions|available\s+(?:commands|options)|commands|options)\s*:\s*$",
    re.IGNORECASE,
)
PAGER_RE = re.compile(
    r"(?:--+\s*more\s*--+|press\s+(?:any\s+key|space)|\(\s*q\s*\)\s*uit)",
    re.IGNORECASE,
)
ERROR_RE = re.compile(
    r"^(?:%|\^|error\b|invalid\b|unrecognized\b|unknown\b|ambiguous\b|"
    r"incomplete\b|syntax\s+error\b|bad\s+command\b|command\s+not\s+found\b)",
    re.IGNORECASE,
)


def clean_terminal_output(value: str) -> str:
    value = ANSI_RE.sub("", value).replace("\r", "")
    while "\x08" in value:
        updated = BACKSPACE_RE.sub("", value)
        if updated == value:
            break
        value = updated
    return value


def option_kind(token: str) -> str:
    lowered = token.lower()
    if lowered in {"<cr>", "<return>", "<enter>", "<[enter]>", "[enter]"}:
        return "cr"
    if (token.startswith("<") and token.endswith(">")) or token.isupper():
        return "parameter"
    if any(char in token for char in "[]{}|"):
        return "parameter"
    return "keyword"


def parse_context_help(
    output: str,
    query: str = "",
    profile: ParserProfile = STRICT_PROFILE,
) -> list[HelpOption]:
    options: list[HelpOption] = []
    seen: set[str] = set()

    for line in clean_terminal_output(output).splitlines():
        stripped = line.strip()
        if not stripped or stripped == "?":
            continue
        if HEADER_RE.match(stripped) or PAGER_RE.search(stripped):
            continue
        # `%` and `^` mark a device error wherever they appear.
        if stripped.startswith(("%", "^")):
            continue
        # A prompt is never indented, so applying the prompt rule to an option
        # row only ever hid options whose description happens to end in `>`.
        indented = line[:1].isspace()
        if not indented and PROMPT_RE.match(stripped):
            continue
        if ERROR_RE.match(stripped) and not (
            indented and profile.error_words_are_commands
        ):
            continue
        if query and stripped.rstrip("?").strip() == query.strip():
            continue
        if query and stripped.rstrip("?").strip().endswith(query.strip()):
            prompt_part = stripped.rstrip("?").strip()[: -len(query.strip())].rstrip()
            if prompt_part.endswith(("#", ">", "$")):
                continue
        match = OPTION_RE.match(line) or CR_ONLY_RE.match(line)
        if match is None and profile.accept_undescribed_options:
            match = TOKEN_ONLY_RE.match(line)
        if not match:
            continue
        token = match.group("token")
        if token in seen:
            continue
        seen.add(token)
        options.append(
            HelpOption(
                token=token,
                description=(match.groupdict().get("description") or "").strip(),
                kind=option_kind(token),
            )
        )
    return options
