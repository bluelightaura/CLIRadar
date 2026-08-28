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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .blueprint import (
    ABSENT,
    PARSED,
    STATE_MARKS,
    TOPPED,
    UNKNOWN,
    missing_blocks,
    node_state,
    rejected_verbs,
)
from .devicecfg import (
    CONVENTIONAL_PORT,
    fetch_host_key,
    host_key_is_pinned,
    load_device_fields,
    pin_host_key,
    probe_reachable,
    save_device_fields,
)
from .prefs import load_prefs, remember_run, save_prefs
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
# `config` and `runs` are the between-starts memory: they are loaded from the
# state file when the launcher opens and written back as they change.
_PREFS: dict[str, object] = {"lang": "en", "theme": "dark", "config": "", "runs": []}


def _lang() -> str:
    return str(_PREFS["lang"])


def _restore_prefs() -> None:
    """Load the remembered language, theme, target and run history."""
    _PREFS.update(load_prefs())


def _persist_prefs() -> None:
    """Write the current preferences out; a failed write is not fatal."""
    save_prefs(dict(_PREFS))


def _remember_target(config_path: Path) -> None:
    """Record the device config the operator is working on, if it is real."""
    try:
        resolved = str(config_path.resolve())
    except OSError:
        resolved = str(config_path)
    if _PREFS.get("config") == resolved:
        return
    _PREFS["config"] = resolved
    _persist_prefs()


def _remembered_target(config_path: Path) -> Path:
    """The remembered target, used only when the given path does not exist.

    An explicit `--config` (or a config.yml sitting in the working directory)
    always wins: memory fills the gap for someone who runs `cliradar` from an
    unrelated directory, and never overrides a path that is actually there.
    """
    if config_path.exists():
        return config_path
    remembered = str(_PREFS.get("config") or "")
    if remembered and Path(remembered).exists():
        return Path(remembered)
    return config_path


def _run_stamp() -> str:
    """Local time, minute resolution - enough to place a run in the day."""
    import time

    return time.strftime("%Y-%m-%d %H:%M", time.localtime())


def _remember_run(mode: str) -> None:
    """Push a launched run onto the history shown under the target line."""
    remember_run(_PREFS, mode, _run_stamp())
    _persist_prefs()


def _last_run_line() -> str:
    """The most recent run as "Audit device · 2026-08-27 21:04", or empty."""
    runs = _PREFS.get("runs") or []
    if not isinstance(runs, list) or not runs:
        return ""
    last = runs[0]
    if not isinstance(last, dict) or not last.get("mode"):
        return ""
    title = t(f"{last['mode']}_title")
    return f"{title} · {last.get('at', '')}".strip(" ·")


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
    "last_run": {"en": "last run", "ru": "прошлый прогон"},
    "bp_unknown": {"en": "not looked at", "ru": "не проверено"},
    "bp_absent": {"en": "not on this device", "ru": "нет на девайсе"},
    "legend": {
        "en": "○ not looked  ⊙ top only  ✓ parsed  · absent",
        "ru": "○ не проверено  ⊙ верхушка  ✓ распарсен  · нет",
    },
    # The progress line a run prints while it works. It is drawn by the CLI on
    # the normal screen, but it speaks the launcher's language: the person who
    # started the run from the menu is the person reading it.
    "st_crawl": {"en": "queries", "ru": "запросов"},
    "st_verify": {"en": "verifying copies", "ru": "проверка копий"},
    "st_probe": {"en": "mode probes", "ru": "пробы режимов"},
    "st_docs": {"en": "reading docs", "ru": "читаю доки"},
    "pr_queue": {"en": "queued", "ru": "в очереди"},
    "pr_left": {"en": "left", "ru": "осталось"},
    "pr_now": {"en": "now", "ru": "сейчас"},
    "pr_saving": {"en": "writing catalog", "ru": "запись каталога"},
    "run_running": {"en": "running", "ru": "идёт прогон"},
    "run_cancel": {
        "en": "Ctrl-C — stop and go back to the menu",
        "ru": "Ctrl-C — прервать и вернуться в меню",
    },
    "docs_found": {"en": "found", "ru": "нашёл"},
    "docs_none": {
        "en": "nothing to read here yet - the folder is empty",
        "ru": "читать пока нечего — папка пуста",
    },
    "docs_more": {"en": "and {n} more", "ru": "и ещё {n}"},
    "lang_title": {"en": "Language", "ru": "Язык"},
    "theme_title": {"en": "Theme", "ru": "Тема"},
    "theme_dark": {"en": "dark", "ru": "тёмная"},
    "theme_light": {"en": "light", "ru": "светлая"},
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
    "key_title": {"en": "New device key", "ru": "Новый ключ устройства"},
    "key_question": {
        "en": "Trust this key?  y — yes, pin it   n — no",
        "ru": "Доверять ключу?  y — да, запомнить   n — нет",
    },
    "key_pinned": {"en": "key pinned", "ru": "ключ сохранён"},
    "key_rejected": {
        "en": "key rejected - connection not trusted",
        "ru": "ключ отклонён — соединение не доверено",
    },
    "key_fetch_failed": {
        "en": "could not read the device key",
        "ru": "не удалось получить ключ устройства",
    },
    "done_prompt": {
        "en": "[ Done ]  ↵ — back to menu,  Ctrl-C — quit ",
        "ru": "[ Готово ]  ↵ — назад в меню,  Ctrl-C — выход ",
    },
    "crash_title": {"en": "Something went wrong", "ru": "Что-то пошло не так"},
    "crash_kept": {
        "en": "The menu is still running - press ↵ to go back.",
        "ru": "Меню продолжает работать — нажми ↵, чтобы вернуться.",
    },
    "crash_saved": {"en": "details saved to", "ru": "подробности записаны в"},
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
    """Flip a two-valued preference (language or theme) and remember it."""
    if key == "lang":
        _PREFS["lang"] = "ru" if _PREFS["lang"] == "en" else "en"
    elif key == "theme":
        _PREFS["theme"] = "light" if _PREFS["theme"] == "dark" else "dark"
    else:
        return
    _persist_prefs()


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
    # Head verbs the run may probe in the contexts it starts from. A blueprint
    # block sets this so "open the VLAN block" types vlan commands and nothing
    # else; an ordinary node leaves it empty and probes as usual.
    focus_verbs: tuple[str, ...] = ()


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
    key: str  # setup | audit | compare | docs | check | map | lang | theme
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
    _Item("lang", cycle=True),
    _Item("theme", cycle=True),
)

# The enter-modes toggle used to sit here. It is gone on purpose: the audit is
# a discovery run that enters modes by itself, and a scoped re-scan from the map
# forces the same thing, so the row only ever offered a way to make a run
# useless. `--enter-modes` on the command line is untouched.

# Runs that touch the device stay locked until a connection is proven; the
# offline modes (docs, config check) and the preference rows are always open.
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
    # How much of this node is known: ○ never looked, ⊙ top taken by a skim,
    # ✓ crawled to completion, · looked for and not on this device.
    state: str = UNKNOWN


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


def load_rejected_verbs(catalog_path: Path) -> set[str]:
    """Head verbs the last scan typed and the device refused.

    That refusal is the only honest evidence that a block is not on this device,
    as opposed to never looked for - so it is read from the same catalog the map
    comes from, and a missing or broken file simply proves nothing.
    """
    try:
        import yaml

        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a broken map must not crash the browser
        return set()
    scan = data.get("scan") if isinstance(data, dict) else None
    probes = scan.get("probes") if isinstance(scan, dict) else None
    if not isinstance(probes, list):
        return set()
    return rejected_verbs(item for item in probes if isinstance(item, dict))


def _flatten_map(
    root: ContextNode, transport: str, expanded: set[str],
    rejected: set[str] | None = None,
) -> list[_TreeRow]:
    """Turn the tree into the visible rows, honouring what is expanded.

    The exec and config halves are shown as two headers the person opens; the
    root's own commands live under the exec header, and every child recurses
    beneath whichever side its prompt puts it on.

    Under the config header the blueprint fills in the blocks the map does not
    (yet) carry - vlan, vrf, mlag and the rest - so the tree has shape before
    the first audit and an operator can tell "not looked at" from "not there".
    ``rejected`` are the head verbs a scan typed and the device refused; they
    are what turns a blueprint row from ○ into a grey "not on this device".
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
                state=node_state(node.commands, node.complete),
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
        rows.extend(
            _blueprint_rows(config_children, rejected or set(), ref)
        )
    return rows


def _blueprint_rows(
    config_children: list[ContextNode],
    rejected: set[str],
    ref: Callable[[ContextNode], ContextRef],
) -> list[_TreeRow]:
    """Template rows for the config blocks this map does not show yet.

    Each one is runnable when there is a proven config context to start from:
    the run enters that context and probes only this block's verbs, which is the
    "tap it and the parser goes exactly there" the tree is for. Without such a
    context (no audit has entered config yet) the row is drawn but not runnable
    - there is nowhere to start it from, and inventing one would type commands
    from an unproven position.
    """
    found = [node.label for node in config_children]
    starts = tuple(ref(node) for node in config_children)
    rows: list[_TreeRow] = []
    for block, state in missing_blocks(found, rejected):
        rows.append(
            _TreeRow(
                key=f"blueprint/{block.label}",
                depth=1,
                label=block.label,
                badge="",
                detail=t("bp_absent") if state == ABSENT else t("bp_unknown"),
                expandable=False,
                expanded=False,
                target=(
                    RunTarget(
                        block.label,
                        starts,
                        descend=True,
                        focus_verbs=tuple(sorted(block.verbs)),
                    )
                    if starts and state != ABSENT
                    else None
                ),
                state=state,
            )
        )
    return rows


# Which theme colour each node state is painted in: a parsed block reads as
# done, a topped one as started, an absent one as ruled out.
_STATE_ROLE = {PARSED: "ok", TOPPED: "warn", ABSENT: "dim", UNKNOWN: "dim"}


def _tree_line(row: _TreeRow, selected: bool) -> str:
    """Compose one row: state mark, indented label, then a right-aligned badge.

    The mark is the whole point of the tree at a glance: ○ never looked at, ⊙
    the top was taken by the discovery pass, ✓ crawled to completion, · asked
    for and refused by the device.
    """
    if row.expandable:
        knob = "▾ " if row.expanded else "▸ "
    else:
        knob = "  "
    indent = "  " * row.depth
    mark = STATE_MARKS.get(row.state, "○")
    left = f" {indent}{knob}{mark} {row.label}"
    right = f"{row.detail}  {row.badge} "
    gap = max(1, _TREE_WIDTH - _disp_width(left) - _disp_width(right))
    text = left + " " * gap + right
    if selected:
        return _c(_pad(_strip_ansi(text), _TREE_WIDTH), *_theme()["sel"])
    role = _STATE_ROLE.get(row.state, "dim")
    coloured = (
        f" {indent}{knob}"
        + _c(mark, *_theme()[role])
        + f" {row.label}"
        + " " * gap
        + right
    )
    return _pad(coloured, _TREE_WIDTH)


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
    # The marks carry the whole state of the map, so they are spelled out
    # rather than left as folklore.
    lines.append(row(_pad(_c("  " + t("legend"), *th["dim"]), _TREE_WIDTH)))
    keys = _c("  " + t("keys_tree"), *th["dim"])
    lines.append(row(_pad(keys, _TREE_WIDTH)))
    lines.append(bot)
    return "\n".join(lines)


def _browse_loop(
    root: ContextNode, catalog_name: str, transport: str,
    rejected: set[str] | None = None,
) -> RunTarget | None:
    """The draw-and-read loop, with the screen already set up by the caller."""
    expanded: set[str] = {_EXEC_KEY, _CONFIG_KEY}
    cursor = 0
    crashes = 0
    while True:
        # Guarded like the launcher: a malformed node or a drawing fault shows
        # as a panel and the tree redraws, rather than ending the session.
        try:
            rows = _flatten_map(root, transport, expanded, rejected)
            cursor = max(0, min(cursor, len(rows) - 1))
            sys.stdout.write("\x1b[H\x1b[2J")
            sys.stdout.write(
                _render_map(root, catalog_name, transport, rows, cursor) + "\n"
            )
            sys.stdout.flush()
            key = _read_key()
            crashes = 0  # a keypress landed: the terminal is answering
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
                if current.target is None:
                    continue  # a template block with nowhere proven to start
                return current.target
            elif key in ("quit", "q"):
                return None
        except Exception as exc:  # any failure keeps the tree, shows the fault
            crashes += 1
            if crashes >= _CRASH_LIMIT:
                raise
            _crash_notice(exc, "map browser")


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

    rejected = load_rejected_verbs(catalog_path)
    if not own_screen:
        return _browse_loop(root, catalog_path.name, transport, rejected)
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    try:
        return _browse_loop(root, catalog_path.name, transport, rejected)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


def _render(
    config_path: Path,
    version: str,
    cursor: int,
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
    # The last run, remembered across starts: "what did I do here last time".
    last = _last_run_line()
    if last:
        seen = _c(f"  ↻ {t('last_run')}: {_trim(last, 40)}", *th["dim"])
        lines.append(row(_pad(seen)))
    lines.append(row(_pad("")))

    for index, item in enumerate(_ITEMS):
        selected = index == cursor
        locked = item.key in _GATED_KEYS and not connected
        marker = "▸" if selected else " "
        label = item.title
        hint = item.hint
        if item.key == "lang":
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


def _notice(lines: list[str], wait: bool = True) -> None:
    """Draw a small centred panel on the alt screen; optionally await a key.

    ``wait`` False leaves the panel showing and returns at once, for callers
    that read the answering keypress themselves (the trust-this-key question).
    """
    th = _theme()
    top = _c("╭" + "─" * (_WIDTH + 2) + "╮", *th["border"])
    bot = _c("╰" + "─" * (_WIDTH + 2) + "╯", *th["border"])
    bar = _c("│", *th["border"])
    body = [f"{bar} {_pad(text)} {bar}" for text in lines]
    sys.stdout.write("\x1b[H\x1b[2J")
    sys.stdout.write("\n".join([top, *body, bot]) + "\n")
    sys.stdout.flush()
    if wait:
        _read_key()


# --------------------------------------------------------------------------- #
# Crash guard: a raised exception becomes a panel, never a drop to the shell.
# --------------------------------------------------------------------------- #

# Where a caught failure is written in full. Both `output/` and `*.log` are
# ignored by git, so a traceback carrying device details never reaches a commit.
CRASH_LOG = Path("output/menu-errors.log")

# How many failures in a row a guarded loop absorbs before it gives up and lets
# the error out. Without this a terminal that can no longer be read would spin
# the loop forever, drawing a panel nobody can answer - a hang instead of a
# crash, which is worse. A loop resets the count as soon as one keypress lands.
_CRASH_LIMIT = 3


def _record_crash(exc: BaseException, where: str) -> Path | None:
    """Append the full traceback to the crash log; return its path, or None.

    Best effort by design: the point of the guard is to keep the menu alive, so
    an unwritable directory costs the log, not the session.
    """
    import time
    import traceback

    text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as handle:
            # Local time on purpose: the operator matches this against when the
            # menu misbehaved in front of them, not against a UTC log elsewhere.
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"\n===== {stamp}  [{where}] =====\n{text}")
    except OSError:
        return None
    return CRASH_LOG


def _crash_lines(exc: BaseException, where: str, log_path: Path | None) -> list[str]:
    """The panel body: what broke, the last frames that led there, where it is
    logged. Kept to the panel width so the border stays true."""
    import traceback

    th = _theme()
    inner = _WIDTH - 4
    frames = traceback.extract_tb(exc.__traceback__)[-3:]
    lines = [
        _c("◈ " + t("crash_title"), *th["title"]),
        "",
        _c("  " + _trim(f"{type(exc).__name__}: {exc}", inner), *th["bad"]),
        _c("  " + _trim(where, inner), *th["dim"]),
        "",
    ]
    for frame in frames:
        spot = f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        lines.append(_c("  " + _trim(spot, inner), *th["dim"]))
    lines.append("")
    if log_path is not None:
        note = f"{t('crash_saved')} {log_path}"
        lines.append(_c("  " + _trim(note, inner), *th["dim"]))
    lines.append(_c("  " + t("crash_kept"), *th["warn"]))
    return lines


def _crash_notice(exc: BaseException, where: str) -> None:
    """Show a caught failure as a panel and wait for a key.

    Every step is defensive: a menu that crashed once may well be in a state
    where drawing crashes too, and a guard that raises would defeat itself.
    """
    import contextlib

    log_path = _record_crash(exc, where)
    try:
        _notice(_crash_lines(exc, where, log_path))
    except Exception:  # noqa: BLE001 - the guard must never raise in its turn
        with contextlib.suppress(Exception):  # the plainest possible fallback
            sys.stdout.write(f"\r\n{type(exc).__name__}: {exc}\r\n")
            sys.stdout.flush()


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


def stage_label(stage: str) -> str:
    """The launcher's word for a crawl stage, for the run's progress line."""
    return t(f"st_{stage}") if f"st_{stage}" in STRINGS else stage


def run_banner(config_path: Path, mode_key: str) -> str:
    """The header a launcher-started run prints before it begins.

    The scan itself draws on the normal screen (its summary has to survive the
    menu closing), so the run gets the launcher's frame here instead: what is
    running, against what, and - the part people kept asking for - that Ctrl-C
    stops it and lands back in the menu rather than in the shell.
    """
    th = _theme()
    top = _c("╭" + "─" * (_WIDTH + 2) + "╮", *th["border"])
    bot = _c("╰" + "─" * (_WIDTH + 2) + "╯", *th["border"])
    bar = _c("│", *th["border"])
    title = _c(f"▸ {t(f'{mode_key}_title')}", *th["title"])
    target = _c(f"  {t('target')}{_target_line(config_path)}", *th["dim"])
    hint = _c("  " + t("run_cancel"), *th["warn"])
    rows = [_pad(title), _pad(target), _pad(hint)]
    return "\n".join([top, *[f"{bar} {row} {bar}" for row in rows], bot])


# How many manual names to spell out before collapsing the rest into a count.
_DOCS_LISTED = 6


def list_doc_files(docs_dir: Path) -> list[str]:
    """Readable manuals under the docs folder, as paths relative to it.

    Only the suffixes the documentation scan actually parses are listed, so the
    panel and the run agree on what counts as a manual. Sorted for a stable
    panel; an unreadable folder simply lists nothing.
    """
    from .docs import SUPPORTED_SUFFIXES

    try:
        found = [
            item.relative_to(docs_dir).as_posix()
            for item in docs_dir.rglob("*")
            if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
        ]
    except OSError:
        return []
    return sorted(found)


def _docs_found_lines(docs_dir: Path) -> list[str]:
    """The "found: a.txt, b.md" block for the docs panel."""
    th = _theme()
    names = list_doc_files(docs_dir)
    if not names:
        return [_c("  ○ " + t("docs_none"), *th["warn"]), ""]
    shown = ", ".join(names[:_DOCS_LISTED])
    rest = len(names) - _DOCS_LISTED
    lines = [
        _c(f"  ✓ {t('docs_found')} ({len(names)}):", *th["ok"]),
        _c("    " + _trim(shown, _WIDTH - 4), *th["dim"]),
    ]
    if rest > 0:
        lines.append(_c("    " + t("docs_more").format(n=rest), *th["dim"]))
    lines.append("")
    return lines


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
    # Name the manuals that will actually be read. "I dropped the file in but
    # the run found nothing" was guesswork before; now the panel says outright
    # what is there, in the same order the scan walks it.
    lines.extend(_docs_found_lines(docs_dir))
    lines.append(_c("  " + t("continue"), *th["dim"]))
    _notice(lines)


def _open_map(config_path: Path, version: str) -> MenuSelection | None:
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
    _remember_run("map")
    return MenuSelection(mode="audit", config=config_path, run_target=target)


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
        # Trimmed, not just padded: an error text longer than the panel would
        # otherwise push the right border out of true.
        lines.append(row(_pad(_c("  " + _trim(message, _WIDTH - 2), *codes))))
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


def _ensure_host_key(
    config_path: Path, fields: dict[str, object], host: str, port: int
) -> tuple[bool, str]:
    """Make sure the device's SSH key is pinned, asking the operator once.

    Trust-on-first-use with a human in the loop: the key is fetched without
    logging in, its SHA256 fingerprint is shown, and only an explicit `y` pins
    it - into a known_hosts file kept beside the config, which is then recorded
    in the config so the session layer verifies every later connection against
    it. An already-pinned target asks nothing.
    """
    known_hosts = Path(str(fields.get("known_hosts") or "")) if fields.get(
        "known_hosts"
    ) else config_path.resolve().parent / "known_hosts"
    if host_key_is_pinned(known_hosts, host, port):
        if not fields.get("known_hosts"):
            fields["known_hosts"] = str(known_hosts)
            save_device_fields(config_path, fields)
        return True, ""
    key, error = fetch_host_key(host, port)
    if key is None:
        return False, f"{t('key_fetch_failed')}: {error}"
    th = _theme()
    _notice(
        [
            _c("◈ " + t("key_title"), *th["title"]),
            "",
            _c(f"  {host}:{port}", *th["dim"]),
            _c(f"  {key['type']}", *th["dim"]),
            _c("  " + key["fingerprint"], *th["ok"]),
            "",
            _c("  " + t("key_question"), *th["warn"]),
        ],
        wait=False,
    )
    answer = _read_key()
    if answer != "y":
        return False, t("key_rejected")
    pin_host_key(known_hosts, host, port, key)
    fields["known_hosts"] = str(known_hosts)
    save_device_fields(config_path, fields)
    return True, ""


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
    crashes = 0
    while True:
        # Guarded like the launcher: saving, the reach test and the host-key
        # question all talk to the outside world, and a raised error there used
        # to end the whole session. Now it becomes a panel and the form stays.
        try:
            sys.stdout.write("\x1b[H\x1b[2J")
            sys.stdout.write(
                _render_setup(config_path, fields, cursor, message, message_role) + "\n"
            )
            sys.stdout.flush()
            key = _read_key()
            crashes = 0  # a keypress landed: the terminal is answering
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
                trusted = True
                if ok and str(fields.get("transport", "ssh")) == "ssh":
                    trusted, trust_note = _ensure_host_key(
                        config_path, fields, host, port
                    )
                    if not trusted:
                        note = trust_note
                has_login = bool(fields.get("username")) and bool(host)
                if ok and trusted and has_login and _password_is_set(fields):
                    connected = True
                    connected_to = f"{fields.get('username')}@{host}"
                    message, message_role = t("conn_ready"), "ok"
                elif ok and trusted and not _password_is_set(fields):
                    connected = False
                    message, message_role = t("need_password"), "warn"
                else:
                    connected = False
                    message, message_role = note, "bad"
        except Exception as exc:  # any failure keeps the form, shows the fault
            crashes += 1
            if crashes >= _CRASH_LIMIT:
                raise
            _crash_notice(exc, f"setup · {_SETUP_ROWS[cursor]}")
            message = f"{type(exc).__name__}: {exc}"
            message_role = "bad"


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
    # Language, theme and the last target come back from the previous start;
    # a target actually present on disk still wins over the remembered one.
    _restore_prefs()
    config_path = _remembered_target(config_path)
    _remember_target(config_path)
    connected = False
    connected_to = ""
    crashes = 0
    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        while True:
            # Every pass is guarded: a failure anywhere below - drawing, the
            # setup screen, the map browser - is shown as a panel and the
            # launcher redraws, instead of the traceback dumping the operator
            # back to the shell mid-session.
            try:
                sys.stdout.write("\x1b[H\x1b[2J")  # home + clear
                sys.stdout.write(
                    _render(config_path, version, cursor, connected, connected_to)
                    + "\n"
                )
                sys.stdout.flush()
                key = _read_key()
                crashes = 0  # a keypress landed: the terminal is answering
                if key in ("up", "k"):
                    cursor = (cursor - 1) % len(_ITEMS)
                elif key in ("down", "j"):
                    cursor = (cursor + 1) % len(_ITEMS)
                elif key == "e":
                    config_path = _pick_target(config_path, version)
                    _remember_target(config_path)
                elif key == "quit" or key == "q":
                    return None
                elif key == "enter":
                    item = _ITEMS[cursor]
                    if item.key in _GATED_KEYS and not connected:
                        continue  # locked until a connection is proven in setup
                    if item.cycle:  # Language / Theme flip in place, no launch
                        _cycle_pref(item.key)
                        continue
                    if item.key == "setup":
                        connected, connected_to = _setup_screen(
                            config_path, connected, connected_to
                        )
                        continue
                    if item.subview:  # "Browse device map" opens the tree in place
                        selection = _open_map(config_path, version)
                        if selection is None:
                            continue  # backed out or no map - stay in the launcher
                        return selection
                    if item.key in ("docs", "compare"):
                        # Both read manuals from the docs folder; reveal it and
                        # spell out what to drop in before the run reads it.
                        _open_docs_folder(docs_dir)
                    return _selection_for(item, config_path)
            except Exception as exc:  # any failure redraws instead of exiting
                crashes += 1
                if crashes >= _CRASH_LIMIT:
                    raise
                _crash_notice(exc, f"menu · {_ITEMS[cursor].key}")
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")  # show cursor, leave alt screen
        sys.stdout.flush()


def _selection_for(item: _Item, config_path: Path) -> MenuSelection:
    """Turn a chosen row into the run the caller launches, and record it."""
    _remember_run(item.key)
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
    return MenuSelection(mode=mode, config=config_path)


def _fallback_menu(config_path: Path, version: str) -> MenuSelection | None:
    """A plain numbered prompt for terminals without raw-mode key reading.

    Only the launching items are offered: the subviews (setup, map browser)
    need raw-mode keys, which is exactly what this fallback is for the absence
    of, so they are left out rather than printed and then rejected.
    """
    print(f"CLIRadar {version} - {_target_line(config_path)} ({config_path.name})")
    launchable = [item for item in _ITEMS if not item.subview and not item.cycle]
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
    return _selection_for(item, config_path)
