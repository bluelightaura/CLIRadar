"""A small interactive launcher shown when cliradar is started with no mode.

The menu exists to make the common runs reachable without remembering the
positional mode and the flags: it is drawn only when a person is actually at a
terminal, and it hands its choice straight back to the ordinary argument flow,
so a piped or scripted invocation behaves exactly as before. Everything here is
standard library - no curses, no third-party UI - to keep the tool light and to
degrade cleanly on a terminal that cannot be put into raw mode.
"""

from __future__ import annotations

import getpass
import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .devicecfg import (
    CONVENTIONAL_PORT,
    load_device_fields,
    probe_reachable,
    save_device_fields,
)
from .tree import (
    ContextNode,
    build_context_tree,
    estimate_seconds,
    format_duration,
    split_top_blocks,
    subtree_commands,
)

# The panel's inner width. Content is padded to this so the right border stays
# straight regardless of a row's own length. Wide enough for the Russian labels,
# which run longer than their English counterparts.
_WIDTH = 56

# The map browser is wider than the launcher: a context path plus its badge does
# not fit the launcher's column, and the tree reads better with room to indent.
_TREE_WIDTH = 60

# An upper bound on the target picker so a directory full of unrelated YAML does
# not turn the launcher into an endless list.
_MAX_TARGETS = 40


# --------------------------------------------------------------------------- #
# Preferences: language and theme, held for the life of the menu session.
# --------------------------------------------------------------------------- #

# Mutable session state. The launcher's two toggles flip these; every render
# reads them, so a keystroke re-language-s or re-themes the whole menu at once.
# English and dark are the stable defaults; the operator switches from there.
_PREFS: dict[str, str] = {"lang": "en", "theme": "dark"}


def _lang() -> str:
    return _PREFS["lang"]


# One table, English and Russian side by side. `t` falls back to English, then
# to the key itself, so a missing translation degrades to something readable
# rather than a crash.
STRINGS: dict[str, dict[str, str]] = {
    "tagline": {
        "en": "Map network device CLI commands",
        "ru": "Карта CLI-команд сетевого устройства",
    },
    "target": {"en": "Target: ", "ru": "Устройство: "},
    "not_configured": {"en": "not configured", "ru": "не настроено"},
    "audit_title": {"en": "Audit device", "ru": "Аудит устройства"},
    "audit_hint": {"en": "inventory over SSH", "ru": "инвентаризация по SSH"},
    "compare_title": {"en": "Compare vs docs", "ru": "Сверка с доками"},
    "compare_hint": {"en": "device truth ⇄ manuals", "ru": "устройство ⇄ мануалы"},
    "docs_title": {"en": "Parse docs only", "ru": "Парсинг доков"},
    "docs_hint": {"en": "offline, no SSH", "ru": "оффлайн, без SSH"},
    "check_title": {"en": "Validate config", "ru": "Проверить конфиг"},
    "check_hint": {"en": "check & exit", "ru": "проверка и выход"},
    "setup_title": {"en": "Set up device", "ru": "Настройка"},
    "setup_hint": {"en": "host, login, connect", "ru": "адрес, логин, связь"},
    "map_title": {"en": "Browse device map", "ru": "Карта устройства"},
    "map_hint": {"en": "pick a block to re-scan", "ru": "выбор блока для скана"},
    "enter_title": {"en": "Enter-modes", "ru": "Вход в режимы"},
    "enter_hint": {"en": "probes contexts (writes)", "ru": "щупает режимы (пишет)"},
    "lang_title": {"en": "Language", "ru": "Язык"},
    "theme_title": {"en": "Theme", "ru": "Тема"},
    "theme_dark": {"en": "dark", "ru": "тёмная"},
    "theme_light": {"en": "light", "ru": "светлая"},
    "on": {"en": "on", "ru": "вкл"},
    "off": {"en": "off", "ru": "выкл"},
    "keys_launcher": {
        "en": "↑/↓ move   ↵ run   e target   q quit",
        "ru": "↑/↓ выбор   ↵ пуск   e устройство   q выход",
    },
    "keys_tree": {
        "en": "↑/↓ move  →/← open  ↵ run block  q back",
        "ru": "↑/↓ выбор  →/← раскрыть  ↵ прогнать  q назад",
    },
    "keys_picker": {
        "en": "↑/↓ move   ↵ select   q back",
        "ru": "↑/↓ выбор   ↵ выбрать   q назад",
    },
    "choose_target": {"en": "Choose a target", "ru": "Выбор устройства"},
    "no_configs": {
        "en": "no device configs found here",
        "ru": "конфигов устройств не найдено",
    },
    "enter_path": {"en": "Enter a path…", "ru": "Ввести путь…"},
    "device_map": {"en": "Device map", "ru": "Карта устройства"},
    "exec_mode": {"en": "Exec mode", "ru": "Обычный режим"},
    "config_mode": {"en": "Config mode", "ru": "Режим конфигурации"},
    "commands": {"en": "commands", "ru": "команд"},
    "full_rescan": {"en": "full re-scan", "ru": "полный перескан"},
    "no_map_title": {"en": "No device map yet", "ru": "Карты устройства ещё нет"},
    "no_map_expected": {"en": "Expected at", "ru": "Ожидается в"},
    "no_map_hint": {
        "en": "Run 'Audit device' once to build it.",
        "ru": "Запусти «Аудит устройства», чтобы её построить.",
    },
    "back": {"en": "↵ back", "ru": "↵ назад"},
    "setup_heading": {"en": "Set up device", "ru": "Настройка устройства"},
    "f_host": {"en": "Host", "ru": "Адрес"},
    "f_port": {"en": "Port", "ru": "Порт"},
    "f_username": {"en": "Username", "ru": "Логин"},
    "f_transport": {"en": "Transport", "ru": "Транспорт"},
    "f_password": {"en": "Password", "ru": "Пароль"},
    "pw_set": {"en": "set", "ru": "задан"},
    "pw_unset": {"en": "not set", "ru": "не задан"},
    "save_test": {"en": "Save & test connection", "ru": "Сохранить и проверить связь"},
    "testing": {"en": "Testing", "ru": "Проверка"},
    "conn_ok": {"en": "connected", "ru": "связь есть"},
    "conn_ready": {
        "en": "device ready - audit & compare unlocked",
        "ru": "устройство готово — аудит и сверка разблокированы",
    },
    "need_password": {
        "en": "reachable, but set a password to run",
        "ru": "пинг есть, но задай пароль для прогона",
    },
    "connected_to": {"en": "connected", "ru": "подключено"},
    "locked_hint": {
        "en": "set up the device first",
        "ru": "сначала настрой устройство",
    },
    "keys_setup": {
        "en": "↑/↓ move   ↵ edit / run   q back",
        "ru": "↑/↓ выбор   ↵ править / пуск   q назад",
    },
    "prompt_value": {"en": "value", "ru": "значение"},
    "docs_folder_title": {"en": "Documentation folder", "ru": "Папка документации"},
    "docs_folder_put": {
        "en": "Put your docs (.txt / .md / .rst) here:",
        "ru": "Положи документацию (.txt / .md / .rst) сюда:",
    },
    "docs_opened": {
        "en": "opened in your file manager",
        "ru": "открыто в файловом менеджере",
    },
    "continue": {"en": "↵ continue", "ru": "↵ продолжить"},
    "done_prompt": {
        "en": "[ Done ]  ↵ — back to menu,  Ctrl-C — quit ",
        "ru": "[ Готово ]  ↵ — назад в меню,  Ctrl-C — выход ",
    },
}


def t(key: str) -> str:
    """Translate a string id into the current language, degrading gracefully."""
    entry = STRINGS.get(key, {})
    return entry.get(_lang()) or entry.get("en") or key


# --------------------------------------------------------------------------- #
# Theme: role -> SGR codes, one palette per theme.
# --------------------------------------------------------------------------- #

_THEMES: dict[str, dict[str, tuple[str, ...]]] = {
    # Cyan frame on a dark background; the selection is black-on-cyan.
    "dark": {
        "border": ("36",),
        "title": ("1",),
        "dim": ("2",),
        "sel": ("30", "46"),
        "warn": ("33",),
        "ok": ("1", "32"),
        "bad": ("31",),
    },
    # Blue frame for a light terminal; the selection is white-on-blue so it
    # stays legible where a dim cyan would wash out.
    "light": {
        "border": ("34",),
        "title": ("1",),
        "dim": ("90",),
        "sel": ("97", "44"),
        "warn": ("33",),
        "ok": ("1", "32"),
        "bad": ("31",),
    },
}


def _theme() -> dict[str, tuple[str, ...]]:
    return _THEMES.get(_PREFS["theme"], _THEMES["dark"])


def _cycle_pref(key: str) -> None:
    """Flip a two-valued preference (language or theme) to its other value."""
    if key == "lang":
        _PREFS["lang"] = "ru" if _PREFS["lang"] == "en" else "en"
    elif key == "theme":
        _PREFS["theme"] = "light" if _PREFS["theme"] == "dark" else "dark"


@dataclass(frozen=True)
class ContextRef:
    """Enough of a recorded context to place a session back in it and crawl.

    ``entry_path`` is the command sequence that reaches the context from the
    root, exactly as the audit recorded it; a scoped scan replays it to stand in
    the context. ``fingerprint`` is the prompt that proves the session arrived.
    """

    name: str
    fingerprint: str
    entry_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunTarget:
    """What the person chose to (re-)scan from the map browser.

    ``starts`` are the contexts a scoped scan begins at - one for a single node,
    the whole config side for that header, or the root for the exec-only choice.
    ``descend`` False scans exactly those contexts without walking the modes they
    open, which is how "the exec mode, not the config under it" is expressed.
    """

    label: str
    starts: tuple[ContextRef, ...]
    descend: bool = True
    group: bool = False


@dataclass(frozen=True)
class MenuSelection:
    """What the person chose - applied onto the parsed args by the caller."""

    mode: str | None = None
    check_config: bool = False
    enter_modes: bool = False
    config: Path | None = None
    run_target: RunTarget | None = None
    # A discovery audit maps the mode structure with a shallow crawl, then the
    # caller drops into the tree so the operator parses blocks on demand.
    discover: bool = False
    browse_after: bool = False


@dataclass
class _Item:
    key: str  # setup | audit | compare | docs | check | map | enter | lang | theme
    toggle: bool = False  # a row that flips a boolean flag (enter-modes)
    subview: bool = False  # a row that opens another screen (setup, map)
    cycle: bool = False  # a row that cycles a preference value (lang, theme)

    @property
    def title(self) -> str:
        return t(f"{self.key}_title")

    @property
    def hint(self) -> str:
        return t(f"{self.key}_hint")


_ITEMS: tuple[_Item, ...] = (
    _Item("setup", subview=True),
    _Item("audit"),
    _Item("compare"),
    _Item("docs"),
    _Item("check"),
    _Item("map", subview=True),
    _Item("enter", toggle=True),
    _Item("lang", cycle=True),
    _Item("theme", cycle=True),
)

# Runs that touch the device stay locked until a connection is proven; the
# offline modes (docs, config check) and the toggles are always reachable.
_GATED_KEYS = frozenset({"audit", "compare"})


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, *codes: str) -> str:
    """Wrap text in SGR codes, or return it bare when colour is off."""
    if not codes or not _use_color():
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def _strip_ansi(text: str) -> str:
    """Drop SGR escapes so a row can be repainted in a single background."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            end = text.find("m", i)
            i = len(text) if end == -1 else end + 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _disp_width(text: str) -> int:
    """Columns a string occupies, ignoring any SGR escapes it carries."""
    width = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\x1b":  # skip an escape sequence up to its final letter
            end = text.find("m", i)
            i = len(text) if end == -1 else end + 1
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        i += 1
    return width


def _pad(text: str, width: int = _WIDTH) -> str:
    return text + " " * max(0, width - _disp_width(text))


def _trim(text: str, width: int) -> str:
    """Cut plain text to at most `width` display columns (no ANSI expected)."""
    out: list[str] = []
    used = 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + step > width:
            break
        out.append(char)
        used += step
    return "".join(out)


def _target_line(config_path: Path) -> str:
    """A best-effort "user@host" read straight from the YAML, for display only.

    This never validates the configuration - "Validate config" is the menu item
    for that. A malformed or missing file simply shows a neutral placeholder so
    the menu still opens.
    """
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        device = data.get("device", {}) if isinstance(data, dict) else {}
        host = device.get("host")
        user = device.get("username")
    except Exception:  # noqa: BLE001 - display only, must never crash the menu
        return t("not_configured")
    if host:
        return f"{user}@{host}" if user else str(host)
    return t("not_configured")


def _password_env(config_path: Path) -> str:
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        device = data.get("device", {}) if isinstance(data, dict) else {}
        return str(device.get("password_env", "SWITCH_PASSWORD"))
    except Exception:  # noqa: BLE001 - display only, must never crash the menu
        return "SWITCH_PASSWORD"


def _catalog_and_transport(config_path: Path) -> tuple[Path, str]:
    """Where the device audit writes its catalog, and over which transport.

    The map browser reads the catalog a prior audit left, so it needs the same
    ``output.device_catalog`` path the run wrote to; the transport only tunes the
    ETA estimate. Both fall back to the conventional defaults when the config is
    absent or does not spell them out, so 'Browse device map' still opens.
    """
    catalog = Path("output/cli_real.yml")
    transport = "ssh"
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        device = data.get("device", {}) if isinstance(data, dict) else {}
        transport = str(device.get("transport", "ssh")).lower()
        output = data.get("output", {}) if isinstance(data, dict) else {}
        if isinstance(output, dict) and output.get("device_catalog"):
            catalog = Path(str(output["device_catalog"]))
    except Exception:  # noqa: BLE001 - display path only, must never crash the menu
        pass
    return catalog, transport


def _target_meta(config_path: Path) -> dict[str, object] | None:
    """Parse just enough of a config to describe its target, or None.

    Returns None when the file is absent, not a mapping, or carries no
    'device.host' - the marker that this YAML is a CLIRadar config at all, which
    is how the picker tells device configs apart from any other YAML lying
    beside them. The transport's conventional port fills in when unset.
    """
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - discovery must never crash the menu
        return None
    if not isinstance(data, dict):
        return None
    device = data.get("device")
    if not isinstance(device, dict) or not device.get("host"):
        return None
    transport = str(device.get("transport", "ssh")).lower()
    port = device.get("port")
    if port is None:
        port = 23 if transport == "telnet" else 22
    return {
        "user": device.get("username"),
        "host": str(device.get("host")),
        "transport": transport,
        "port": port,
    }


def _target_label(config_path: Path) -> str:
    """A one-line 'user@host  transport:port' for the picker, filename aside."""
    meta = _target_meta(config_path)
    if meta is None:
        return "no device section"
    who = f"{meta['user']}@{meta['host']}" if meta["user"] else str(meta["host"])
    return f"{who}  {meta['transport']}:{meta['port']}"


def _discover_targets(config_path: Path, base: Path | None = None) -> list[Path]:
    """Device configs found beside the tool: *.yml/*.yaml carrying a device.

    Looks in the working directory, an optional 'configs/' folder, and the
    directory of the current config, so the common layouts are all covered
    without any setup. The list is de-duplicated by real path and capped; the
    current config is kept at the front even if it has yet to be filled in, so
    the picker never loses the file the person arrived with.
    """
    base = base or Path.cwd()
    found: dict[Path, Path] = {}
    for root in (base, base / "configs", config_path.parent):
        try:
            entries = sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
        except OSError:
            continue
        for path in entries:
            key = path.resolve()
            if key in found or _target_meta(path) is None:
                continue
            found[key] = path
            if len(found) >= _MAX_TARGETS:
                break
    targets = list(found.values())
    current = config_path.resolve()
    if current not in found and config_path.exists():
        targets.insert(0, config_path)
    return targets


def _render_picker(
    targets: list[Path], current: Path, cursor: int, version: str
) -> str:
    th = _theme()

    def border(s: str) -> str:
        return _c(s, *th["border"])

    top = border("╭" + "─" * (_WIDTH + 2) + "╮")
    mid = border("├" + "─" * (_WIDTH + 2) + "┤")
    bot = border("╰" + "─" * (_WIDTH + 2) + "╯")
    bar = border("│")

    def row(inner: str) -> str:
        return f"{bar} {inner} {bar}"

    lines = [top]
    lines.append(row(_pad(_c("◈ ", *th["border"]) + _c(t("choose_target"), *th["title"]))))
    lines.append(mid)
    if not targets:
        lines.append(row(_pad(_c("  " + t("no_configs"), *th["dim"]))))
    for index, path in enumerate(targets):
        marker = "▸" if index == cursor else " "
        text = f" {marker} {_pad(_target_label(path), 30)} {path.name}"
        text = _c(_pad(_strip_ansi(text)), *th["sel"]) if index == cursor else _pad(text)
        lines.append(row(text))
    manual = len(targets)  # the "type a path" row sits after the discovered ones
    marker = "▸" if cursor == manual else " "
    text = f" {marker} ✎ {t('enter_path')}"
    text = _c(_pad(_strip_ansi(text)), *th["sel"]) if cursor == manual else _pad(text)
    lines.append(row(text))
    lines.append(mid)
    lines.append(row(_pad(_c("  " + t("keys_picker"), *th["dim"]))))
    lines.append(bot)
    return "\n".join(lines)


def _pick_target(config_path: Path, version: str, base: Path | None = None) -> Path:
    """Let the person choose a device config, or type a path; returns the pick.

    Falls back to returning the current config unchanged when the person backs
    out, so 'e' is always safe to press.
    """
    targets = _discover_targets(config_path, base)
    cursor = next(
        (i for i, t in enumerate(targets) if t.resolve() == config_path.resolve()),
        0,
    )
    rows = len(targets) + 1  # discovered targets plus the manual-entry row
    while True:
        sys.stdout.write("\x1b[H\x1b[2J")
        sys.stdout.write(_render_picker(targets, config_path, cursor, version) + "\n")
        sys.stdout.flush()
        key = _read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % rows
        elif key in ("down", "j"):
            cursor = (cursor + 1) % rows
        elif key in ("quit", "q"):
            return config_path
        elif key == "enter":
            if cursor == len(targets):  # the manual-entry row
                typed = _prompt_line(f"Config file [{config_path}]: ")
                return Path(typed) if typed else config_path
            return targets[cursor]


# --------------------------------------------------------------------------- #
# Map browser: walk the tree a prior audit left, pick a subtree to re-scan.
# --------------------------------------------------------------------------- #

# The two synthetic headers the tree hangs under. They are not recorded
# contexts, so their keys carry a sigil no context name can collide with.
_EXEC_KEY = "@exec"
_CONFIG_KEY = "@config"


@dataclass
class _TreeRow:
    """One printed line of the map: a group header or a real context."""

    key: str
    depth: int
    label: str
    badge: str  # the ETA plate, e.g. "~2m30s"
    detail: str  # a right-of-label note, e.g. "1539 cmds"
    expandable: bool
    expanded: bool
    target: RunTarget | None  # what "run this block" would launch from here


def load_context_map(catalog_path: Path) -> ContextNode | None:
    """Build the browsable tree from an audit catalog, or None if there is none.

    Reads only the ``scan.contexts`` list a mode scan persisted; a catalog from
    a run that never entered any context still yields a one-node exec tree. Any
    read or parse failure returns None so the caller can fall back to "run an
    audit first" rather than crash the menu.
    """
    try:
        import yaml

        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a missing or broken map must not crash us
        return None
    if not isinstance(data, dict):
        return None
    scan = data.get("scan")
    contexts = scan.get("contexts") if isinstance(scan, dict) else None
    if not contexts:
        return None
    return build_context_tree(contexts)


def _flatten_map(
    root: ContextNode, transport: str, expanded: set[str]
) -> list[_TreeRow]:
    """Turn the tree into the visible rows, honouring what is expanded.

    The exec and config halves are shown as two headers the person opens; the
    root's own commands live under the exec header, and every child recurses
    beneath whichever side its prompt puts it on.
    """
    exec_children, config_children = split_top_blocks(root)

    def badge(node: ContextNode) -> str:
        return format_duration(estimate_seconds(node, transport))

    total = estimate_seconds(root, transport)
    config_secs = sum(estimate_seconds(c, transport) for c in config_children)
    exec_secs = max(0.0, total - config_secs)

    def ref(node: ContextNode) -> ContextRef:
        return ContextRef(node.name, node.fingerprint or "", node.entry_path)

    rows: list[_TreeRow] = []

    def add(node: ContextNode, depth: int) -> None:
        is_open = node.name in expanded
        rows.append(
            _TreeRow(
                key=node.name,
                depth=depth,
                label=node.label,
                badge=badge(node),
                detail=f"{subtree_commands(node)} cmds",
                expandable=bool(node.children),
                expanded=is_open,
                # Running a node re-scans it and everything beneath it.
                target=RunTarget(node.label, (ref(node),), descend=True),
            )
        )
        if node.children and is_open:
            for child in sorted(node.children, key=lambda n: n.label):
                add(child, depth + 1)

    rows.append(
        _TreeRow(
            key=_EXEC_KEY,
            depth=0,
            label=t("exec_mode"),
            badge=format_duration(exec_secs),
            detail=f"{root.commands} cmds",
            expandable=True,
            expanded=_EXEC_KEY in expanded,
            # Exec-only: scan the root's own commands, do not walk into config.
            target=RunTarget("exec mode", (ref(root),), descend=False, group=True),
        )
    )
    if _EXEC_KEY in expanded:
        for child in sorted(exec_children, key=lambda n: n.label):
            add(child, 1)

    rows.append(
        _TreeRow(
            key=_CONFIG_KEY,
            depth=0,
            label=t("config_mode"),
            badge=format_duration(config_secs),
            detail=f"{len(config_children)} mode" + ("" if len(config_children) == 1 else "s"),
            expandable=bool(config_children),
            expanded=_CONFIG_KEY in expanded,
            # The whole config side: every config sub-mode and its subtree.
            target=RunTarget(
                "config mode",
                tuple(ref(child) for child in config_children),
                descend=True,
                group=True,
            ),
        )
    )
    if _CONFIG_KEY in expanded:
        for child in sorted(config_children, key=lambda n: n.label):
            add(child, 1)
    return rows


def _tree_line(row: _TreeRow, selected: bool) -> str:
    """Compose one row: marker, indented label, then a right-aligned badge."""
    if row.expandable:
        knob = "▾ " if row.expanded else "▸ "
    else:
        knob = "· "
    indent = "  " * row.depth
    left = f" {indent}{knob}{row.label}"
    right = f"{row.detail}  {row.badge} "
    gap = max(1, _TREE_WIDTH - _disp_width(left) - _disp_width(right))
    text = left + " " * gap + right
    if selected:
        return _c(_pad(_strip_ansi(text), _TREE_WIDTH), *_theme()["sel"])
    # Dim the counts so the label and badge lead the eye; headers stay bold.
    return _pad(text, _TREE_WIDTH)


def _render_map(
    root: ContextNode, target_label: str, transport: str,
    rows: list[_TreeRow], cursor: int,
) -> str:
    th = _theme()

    def border(s: str) -> str:
        return _c(s, *th["border"])

    top = border("╭" + "─" * (_TREE_WIDTH + 2) + "╮")
    mid = border("├" + "─" * (_TREE_WIDTH + 2) + "┤")
    bot = border("╰" + "─" * (_TREE_WIDTH + 2) + "╯")
    bar = border("│")

    def row(inner: str) -> str:
        return f"{bar} {inner} {bar}"

    lines = [top]
    title = (
        _c("◈ ", *th["border"]) + _c(t("device_map"), *th["title"])
        + _c(f"   {target_label}", *th["dim"])
    )
    lines.append(row(_pad(title, _TREE_WIDTH)))
    whole = format_duration(estimate_seconds(root, transport))
    summary = f"{subtree_commands(root)} {t('commands')} · {t('full_rescan')} {whole}"
    lines.append(row(_pad(_c("  " + summary, *th["dim"]), _TREE_WIDTH)))
    lines.append(mid)
    for index, item in enumerate(rows):
        lines.append(row(_tree_line(item, index == cursor)))
    lines.append(mid)
    keys = _c("  " + t("keys_tree"), *th["dim"])
    lines.append(row(_pad(keys, _TREE_WIDTH)))
    lines.append(bot)
    return "\n".join(lines)


def _browse_loop(
    root: ContextNode, catalog_name: str, transport: str
) -> RunTarget | None:
    """The draw-and-read loop, with the screen already set up by the caller."""
    expanded: set[str] = {_EXEC_KEY, _CONFIG_KEY}
    cursor = 0
    while True:
        rows = _flatten_map(root, transport, expanded)
        cursor = max(0, min(cursor, len(rows) - 1))
        sys.stdout.write("\x1b[H\x1b[2J")
        sys.stdout.write(
            _render_map(root, catalog_name, transport, rows, cursor) + "\n"
        )
        sys.stdout.flush()
        key = _read_key()
        current = rows[cursor]
        if key in ("up", "k"):
            cursor = (cursor - 1) % len(rows)
        elif key in ("down", "j"):
            cursor = (cursor + 1) % len(rows)
        elif key in ("right", "l"):
            if current.expandable:
                expanded.add(current.key)
        elif key in ("left", "h"):
            expanded.discard(current.key)
        elif key == "enter":
            return current.target
        elif key in ("quit", "q"):
            return None


def browse_map(
    catalog_path: Path, version: str, transport: str = "ssh", own_screen: bool = True
) -> RunTarget | None:
    """Let the person walk the last audit's map and pick a subtree to re-scan.

    Returns the chosen RunTarget, or None when there is no map to show, no
    terminal to draw on, or the person backs out. The two side headers open by
    default so the shape is visible without a keystroke. ``own_screen`` is False
    when the launcher already holds the alternate screen, so the browser draws
    into it instead of entering and leaving a second one.
    """
    root = load_context_map(catalog_path)
    if root is None:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import termios  # noqa: F401 - probing availability of raw-mode reads
    except ImportError:
        return None

    if not own_screen:
        return _browse_loop(root, catalog_path.name, transport)
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    try:
        return _browse_loop(root, catalog_path.name, transport)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


def _render(
    config_path: Path,
    version: str,
    cursor: int,
    enter_on: bool,
    connected: bool = False,
    connected_to: str = "",
) -> str:
    th = _theme()

    def border(s: str) -> str:
        return _c(s, *th["border"])

    top = border("╭" + "─" * (_WIDTH + 2) + "╮")
    mid = border("├" + "─" * (_WIDTH + 2) + "┤")
    bot = border("╰" + "─" * (_WIDTH + 2) + "╯")
    bar = border("│")

    def row(inner: str) -> str:
        return f"{bar} {inner} {bar}"

    lines = [top]
    title = _c("◈ ", *th["border"]) + _c(f"CLIRadar {version}", *th["title"])
    lines.append(row(_pad(title)))
    lines.append(row(_pad(_c("  " + t("tagline"), *th["dim"]))))
    lines.append(mid)
    # Target on the left, config filename on the right; the info side is
    # trimmed if a long name would otherwise push the border out of true.
    info = f"{t('target')}{_target_line(config_path)}"
    name = f"({config_path.name})"
    gap = _WIDTH - _disp_width(info) - _disp_width(name)
    if gap < 1:
        info = _trim(info, max(0, _WIDTH - _disp_width(name) - 1))
        gap = 1
    target_line = _c(info, *th["dim"]) + " " * gap + _c(name, *th["dim"])
    lines.append(row(_pad(target_line)))
    # When a session is proven, the device it reached glows green - the "what am
    # I connected to" line the operator asked for.
    if connected:
        status = _c(f"  ✓ {t('connected_to')}: {connected_to}", *th["ok"])
    else:
        status = _c(f"  ○ {t('not_configured')}", *th["dim"])
    lines.append(row(_pad(status)))
    lines.append(row(_pad("")))

    for index, item in enumerate(_ITEMS):
        selected = index == cursor
        locked = item.key in _GATED_KEYS and not connected
        marker = "▸" if selected else " "
        label = item.title
        hint = item.hint
        if item.key == "enter":
            state = (
                _c(f"[{t('on')}]", *th["ok"]) if enter_on
                else _c(f"[{t('off')}]", *th["dim"])
            )
            hint = f"{item.hint}  {state}"
            label = _c(item.title + " ", *th["warn"]) + _c("⚠", *th["warn"])
        elif item.key == "lang":
            hint = _c(_lang().upper(), *th["title"])
        elif item.key == "theme":
            name = t("theme_dark") if _PREFS["theme"] == "dark" else t("theme_light")
            hint = _c(name, *th["title"])
        elif locked:
            # A locked run reads as unavailable: a padlock, dimmed, no launch.
            label = f"{item.title} ⊘"
            hint = t("locked_hint")
        text = f" {marker} {_pad(label, 18)} {hint}"
        if selected and not locked:
            # Whole-row highlight reads as the current choice; inner SGR resets
            # are stripped first so the background is not cut short.
            text = _c(_pad(_strip_ansi(text)), *th["sel"])
        elif locked:
            text = _c(_pad(_strip_ansi(text)), *th["dim"])
        else:
            text = _pad(text)
        lines.append(row(text))

    lines.append(row(_pad("")))
    keys = _c("  " + t("keys_launcher"), *th["dim"])
    lines.append(row(_pad(keys)))
    lines.append(bot)
    return "\n".join(lines)


def _read_key() -> str:
    """One keypress as a token: up/down/enter/edit/quit, or a bare character.

    Raw mode is entered only for the duration of a single read so a Ctrl-C or a
    terminal that rejects raw mode surfaces immediately rather than wedging.
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char == "\x1b":  # an arrow key arrives as ESC [ A-D, or a bare Esc
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up"
            if seq == "[B":
                return "down"
            if seq == "[C":
                return "right"
            if seq == "[D":
                return "left"
            return "quit"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    if char in ("\r", "\n"):
        return "enter"
    if char == "\x03":  # Ctrl-C
        return "quit"
    return char.lower()


def _prompt_line(message: str) -> str:
    """Read a line on the normal (cooked) terminal, for the edit prompt."""
    sys.stdout.write("\x1b[?1049l")  # leave the alternate screen while typing
    sys.stdout.flush()
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    finally:
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.flush()


def _ensure_password(config_path: Path) -> None:
    """Ask once for the device password if its environment variable is unset.

    The value is only placed in this process's environment, never written
    anywhere; the session layer reads it from there.
    """
    env = _password_env(config_path)
    if os.environ.get(env):
        return
    try:
        secret = getpass.getpass(f"Password for {_target_line(config_path)} ({env}): ")
    except (EOFError, KeyboardInterrupt):
        secret = ""
    if secret:
        os.environ[env] = secret


def prompt_return() -> bool:
    """Pause after a menu-driven run, then say whether to redraw the menu.

    Returns True to loop back to the launcher, False to leave (Ctrl-C or EOF at
    the prompt). The run printed its summary on the normal screen the menu left
    when it handed back the selection, so nothing is hidden behind the panel;
    this pause simply keeps that summary visible until the person is ready.
    """
    try:
        input("\n" + t("done_prompt"))
        return True
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _notice(lines: list[str]) -> None:
    """Draw a small centred panel and wait for one keypress, on the alt screen."""
    th = _theme()
    top = _c("╭" + "─" * (_WIDTH + 2) + "╮", *th["border"])
    bot = _c("╰" + "─" * (_WIDTH + 2) + "╯", *th["border"])
    bar = _c("│", *th["border"])
    body = [f"{bar} {_pad(text)} {bar}" for text in lines]
    sys.stdout.write("\x1b[H\x1b[2J")
    sys.stdout.write("\n".join([top, *body, bot]) + "\n")
    sys.stdout.flush()
    _read_key()


def _launch_file_manager(path: Path) -> bool:
    """Best-effort open of a folder in the desktop file manager; never raises."""
    import shutil
    import subprocess

    for opener in ("xdg-open", "open"):
        if shutil.which(opener):
            try:
                subprocess.Popen(
                    [opener, str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError:
                return False
    return False


def _open_docs_folder(docs_dir: Path) -> None:
    """Reveal the docs folder and tell the operator what to drop into it.

    The compare and docs modes read manuals from here, so before either runs the
    menu makes sure the folder exists, opens it in the file manager, and shows
    the "put your .txt/.doc here" note the operator asked to see spelled out.
    """
    try:
        docs_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    th = _theme()
    opened = _launch_file_manager(docs_dir)
    # Keep the folder name visible: if the path is too long, trim the front, not
    # the tail, so the operator always sees where the "vendor_docs" lands.
    full = str(docs_dir)
    avail = _WIDTH - 3
    if _disp_width(full) > avail:
        full = "…" + full[-(avail - 1):]
    where = "  " + full
    lines = [
        _c("◈ " + t("docs_folder_title"), *th["title"]),
        "",
        _c("  " + t("docs_folder_put"), *th["dim"]),
        _c(where, *th["ok"]),
        "",
    ]
    if opened:
        lines.append(_c("  ✓ " + t("docs_opened"), *th["dim"]))
        lines.append("")
    lines.append(_c("  " + t("continue"), *th["dim"]))
    _notice(lines)


def _open_map(
    config_path: Path, version: str, enter_on: bool
) -> MenuSelection | None:
    """Open the map browser from the launcher and turn the pick into a run.

    Returns None to stay in the launcher: either there is no map to browse yet,
    or the person backed out of it. A chosen subtree becomes an audit carrying
    the RunTarget the caller will scope the scan to.
    """
    catalog, transport = _catalog_and_transport(config_path)
    if load_context_map(catalog) is None:
        th = _theme()
        _notice(
            [
                _c("◈ " + t("no_map_title"), *th["title"]),
                "",
                _c(f"  {t('no_map_expected')} {catalog}", *th["dim"]),
                _c("  " + t("no_map_hint"), *th["dim"]),
                "",
                _c("  " + t("back"), *th["dim"]),
            ]
        )
        return None
    target = browse_map(catalog, version, transport, own_screen=False)
    if target is None:
        return None
    return MenuSelection(
        mode="audit", config=config_path, enter_modes=enter_on, run_target=target
    )


# --------------------------------------------------------------------------- #
# Device setup: fill in the target, then prove it answers before unlocking runs.
# --------------------------------------------------------------------------- #

# Field rows in order, then the two action rows at the bottom of the form.
_SETUP_ROWS: tuple[str, ...] = (
    "host", "port", "username", "transport", "password", "save", "back"
)
_CONNECT_BAR_WIDTH = 34


def _password_is_set(fields: dict[str, object]) -> bool:
    env = str(fields.get("password_env") or "SWITCH_PASSWORD")
    return bool(os.environ.get(env))


def _render_setup(
    config_path: Path,
    fields: dict[str, object],
    cursor: int,
    message: str,
    message_role: str,
) -> str:
    th = _theme()

    def border(s: str) -> str:
        return _c(s, *th["border"])

    top = border("╭" + "─" * (_WIDTH + 2) + "╮")
    mid = border("├" + "─" * (_WIDTH + 2) + "┤")
    bot = border("╰" + "─" * (_WIDTH + 2) + "╯")
    bar = border("│")

    def row(inner: str) -> str:
        return f"{bar} {inner} {bar}"

    def value_for(key: str) -> str:
        if key == "password":
            return t("pw_set") if _password_is_set(fields) else t("pw_unset")
        raw = fields.get(key)
        return "" if raw is None else str(raw)

    lines = [top]
    lines.append(row(_pad(_c("◈ ", *th["border"]) + _c(t("setup_heading"), *th["title"]))))
    lines.append(row(_pad(_c("  " + config_path.name, *th["dim"]))))
    lines.append(mid)

    for index, key in enumerate(_SETUP_ROWS):
        selected = index == cursor
        marker = "▸" if selected else " "
        if key == "save":
            text = f" {marker} {t('save_test')}"
        elif key == "back":
            text = f" {marker} {t('back')}"
        else:
            value = value_for(key) or "—"
            text = f" {marker} {_pad(t('f_' + key), 14)} {value}"
        text = (
            _c(_pad(_strip_ansi(text)), *th["sel"]) if selected else _pad(text)
        )
        lines.append(row(text))

    lines.append(mid)
    if message:
        codes = th.get(message_role, th["dim"])
        lines.append(row(_pad(_c("  " + message, *codes))))
    else:
        lines.append(row(_pad("")))
    lines.append(row(_pad(_c("  " + t("keys_setup"), *th["dim"]))))
    lines.append(bot)
    return "\n".join(lines)


def _draw_connect_progress(host: str, port: int, frac: float) -> None:
    """Repaint a single progress frame while the reach test runs."""
    th = _theme()
    filled = int(round(frac * _CONNECT_BAR_WIDTH))
    meter = "█" * filled + "░" * (_CONNECT_BAR_WIDTH - filled)
    top = _c("╭" + "─" * (_WIDTH + 2) + "╮", *th["border"])
    bot = _c("╰" + "─" * (_WIDTH + 2) + "╯", *th["border"])
    bar = _c("│", *th["border"])

    def row(inner: str) -> str:
        return f"{bar} {inner} {bar}"

    head = _c(f"  {t('testing')} {host}:{port}", *th["title"])
    plate = _c(f"  {meter} {int(frac * 100):3d}%", *th["border"])
    frame = "\n".join([top, row(_pad(head)), row(_pad("")), row(_pad(plate)), bot])
    sys.stdout.write("\x1b[H\x1b[2J")
    sys.stdout.write(frame + "\n")
    sys.stdout.flush()


def _edit_field(config_path: Path, fields: dict[str, object], key: str) -> None:
    """Edit one field on the normal screen, then return to the alt screen."""
    if key == "transport":
        current = str(fields.get("transport", "ssh"))
        new = "telnet" if current == "ssh" else "ssh"
        fields["transport"] = new
        # Follow the conventional port when it still matches the old transport,
        # so a switch from ssh:22 lands on telnet:23 rather than a stale 22.
        if fields.get("port") in (None, CONVENTIONAL_PORT.get(current)):
            fields["port"] = CONVENTIONAL_PORT[new]
        return
    if key == "password":
        env = str(fields.get("password_env") or "SWITCH_PASSWORD")
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        try:
            secret = getpass.getpass(f"{t('f_password')} ({env}): ")
        except (EOFError, KeyboardInterrupt):
            secret = ""
        if secret:
            os.environ[env] = secret
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        return
    label = t("f_" + key)
    current = "" if fields.get(key) is None else str(fields[key])
    typed = _prompt_line(f"{label} [{current}]: ")
    if not typed:
        return
    if key == "port":
        try:
            fields["port"] = int(typed)
        except ValueError:
            pass
    else:
        fields[key] = typed


def _setup_screen(
    config_path: Path, connected: bool, connected_to: str
) -> tuple[bool, str]:
    """Edit the device, save it, and reach-test it; return the connection state.

    The password entered here goes only to the process environment. A successful
    reach test with a password set unlocks the gated runs and lights the device
    green in the launcher; a reachable device with no password says so instead of
    claiming a false readiness.
    """
    fields = load_device_fields(config_path)
    cursor = 0
    message = ""
    message_role = "dim"
    while True:
        sys.stdout.write("\x1b[H\x1b[2J")
        sys.stdout.write(
            _render_setup(config_path, fields, cursor, message, message_role) + "\n"
        )
        sys.stdout.flush()
        key = _read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % len(_SETUP_ROWS)
        elif key in ("down", "j"):
            cursor = (cursor + 1) % len(_SETUP_ROWS)
        elif key in ("quit", "q"):
            return connected, connected_to
        elif key == "enter":
            field = _SETUP_ROWS[cursor]
            if field == "back":
                return connected, connected_to
            if field != "save":
                _edit_field(config_path, fields, field)
                continue
            # Save the non-secret fields, then reach-test the target.
            save_device_fields(config_path, fields)
            host = str(fields.get("host") or "")
            port = int(fields.get("port") or CONVENTIONAL_PORT.get(
                str(fields.get("transport", "ssh")), 22))
            ok, note = probe_reachable(
                host, port, timeout=6.0,
                on_tick=lambda frac: _draw_connect_progress(host, port, frac),
            )
            has_login = bool(fields.get("username")) and bool(host)
            if ok and has_login and _password_is_set(fields):
                connected = True
                connected_to = f"{fields.get('username')}@{host}"
                message, message_role = t("conn_ready"), "ok"
            elif ok and not _password_is_set(fields):
                connected = False
                message, message_role = t("need_password"), "warn"
            else:
                connected = False
                message, message_role = note, "bad"


def interactive_menu(
    config_path: Path, version: str, docs_dir: Path = Path("vendor_docs")
) -> MenuSelection | None:
    """Draw the launcher and return the choice, or None to fall back to usage.

    Returns None when there is no terminal to draw on, when raw-mode key reading
    is unavailable (e.g. Windows), or when the person quits - in every case the
    caller prints its ordinary "choose a mode" message.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import termios  # noqa: F401 - probing availability of raw-mode reads
    except ImportError:
        return _fallback_menu(config_path, version)

    cursor = 0
    enter_on = False
    connected = False
    connected_to = ""
    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        while True:
            sys.stdout.write("\x1b[H\x1b[2J")  # home + clear
            sys.stdout.write(
                _render(config_path, version, cursor, enter_on, connected, connected_to)
                + "\n"
            )
            sys.stdout.flush()
            key = _read_key()
            if key in ("up", "k"):
                cursor = (cursor - 1) % len(_ITEMS)
            elif key in ("down", "j"):
                cursor = (cursor + 1) % len(_ITEMS)
            elif key == "e":
                config_path = _pick_target(config_path, version)
            elif key == "quit" or key == "q":
                return None
            elif key == "enter":
                item = _ITEMS[cursor]
                if item.key in _GATED_KEYS and not connected:
                    continue  # locked until a connection is proven in setup
                if item.toggle:
                    enter_on = not enter_on
                    continue
                if item.cycle:  # Language / Theme flip in place, no launch
                    _cycle_pref(item.key)
                    continue
                if item.key == "setup":
                    connected, connected_to = _setup_screen(
                        config_path, connected, connected_to
                    )
                    continue
                if item.subview:  # "Browse device map" opens the tree in place
                    selection = _open_map(config_path, version, enter_on)
                    if selection is None:
                        continue  # backed out or no map yet - stay in the launcher
                    return selection
                if item.key in ("docs", "compare"):
                    # Both read manuals from the docs folder; reveal it and spell
                    # out what to drop in before the run reads from it.
                    _open_docs_folder(docs_dir)
                return _selection_for(item, config_path, enter_on)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")  # show cursor, leave alt screen
        sys.stdout.flush()


def _selection_for(item: _Item, config_path: Path, enter_on: bool) -> MenuSelection:
    if item.key == "check":
        return MenuSelection(check_config=True, config=config_path)
    mode = item.key  # "audit" | "compare" | "docs"
    if mode in ("audit", "compare"):
        # Restore the terminal first so getpass draws on a normal screen.
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        _ensure_password(config_path)
        sys.stdout.write("\x1b[?1049h")
    if mode == "audit":
        # Audit maps the mode structure (a shallow discovery that must enter
        # config to find blocks like vrf/mlag) and then opens the tree so the
        # operator parses blocks on demand.
        return MenuSelection(
            mode="audit", enter_modes=True, config=config_path,
            discover=True, browse_after=True,
        )
    return MenuSelection(mode=mode, enter_modes=enter_on, config=config_path)


def _fallback_menu(config_path: Path, version: str) -> MenuSelection | None:
    """A plain numbered prompt for terminals without raw-mode key reading.

    Only the launching items are offered: the enter-modes toggle and the map
    browser both need raw-mode keys, which is exactly what this fallback is for
    the absence of, so they are left out rather than printed and then rejected.
    """
    print(f"CLIRadar {version} - {_target_line(config_path)} ({config_path.name})")
    launchable = [item for item in _ITEMS if not item.toggle and not item.subview]
    for index, item in enumerate(launchable, start=1):
        print(f"  {index}) {item.title:18} {item.hint}")
    try:
        raw = input(f"Choose [1-{len(launchable)}], or blank to cancel: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(launchable)):
        return None
    item = launchable[int(raw) - 1]
    if item.key in ("audit", "compare"):
        _ensure_password(config_path)
    return _selection_for(item, config_path, enter_on=False)
