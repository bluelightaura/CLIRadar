from pathlib import Path

import pytest
import yaml

from cliradar import menu
from cliradar.menu import (
    _CONFIG_KEY,
    _EXEC_KEY,
    _GATED_KEYS,
    _ITEMS,
    ContextRef,
    MenuSelection,
    RunTarget,
    _catalog_and_transport,
    _discover_targets,
    _disp_width,
    _edit_field,
    _fallback_menu,
    _flatten_map,
    _password_is_set,
    _render,
    _render_map,
    _render_picker,
    _render_setup,
    _selection_for,
    _strip_ansi,
    _target_label,
    _target_line,
    _target_meta,
    browse_map,
    interactive_menu,
    load_context_map,
)


def _write_cfg(path: Path, device: dict) -> Path:
    path.write_text(yaml.safe_dump({"device": device}))
    return path


def _no_color(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_use_color", lambda: False)


@pytest.fixture(autouse=True)
def _reset_prefs():
    """Keep language/theme deterministic; a test that flips them cannot leak."""
    saved = dict(menu._PREFS)
    menu._PREFS.update(lang="en", theme="dark")
    yield
    menu._PREFS.clear()
    menu._PREFS.update(saved)


def test_strip_ansi_removes_only_escapes() -> None:
    assert _strip_ansi("\x1b[36m│\x1b[0m text \x1b[1mX\x1b[0m") == "│ text X"


def test_disp_width_ignores_escapes_and_counts_wide_chars() -> None:
    assert _disp_width("\x1b[36mabc\x1b[0m") == 3
    # A neutral-width symbol used in the panel stays one column.
    assert _disp_width("⚠") == 1


def test_target_line_reads_user_and_host(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text(yaml.safe_dump({"device": {"host": "10.0.0.1", "username": "op"}}))
    assert _target_line(cfg) == "op@10.0.0.1"


def test_target_line_is_neutral_when_missing_or_broken(tmp_path: Path) -> None:
    assert _target_line(tmp_path / "absent.yml") == "not configured"
    broken = tmp_path / "b.yml"
    broken.write_text("device: [this is not a mapping")
    assert _target_line(broken) == "not configured"


def test_render_shows_every_item_and_marks_the_cursor(monkeypatch) -> None:
    _no_color(monkeypatch)
    # cursor 2 is Compare in the new order (setup, audit, compare, ...).
    frame = _render(Path("config.yml"), "9.9.9", cursor=2)
    assert "CLIRadar 9.9.9" in frame
    for item in _ITEMS:
        assert item.title in frame
    # Only the cursor row carries the pointer.
    pointed = [line for line in frame.splitlines() if "▸" in line]
    assert len(pointed) == 1
    assert "Compare vs docs" in pointed[0]


def test_launcher_has_no_enter_modes_row(monkeypatch) -> None:
    # The toggle was removed: the audit enters modes on its own and a scoped
    # re-scan forces it, so the row could only ever turn a run into a no-op.
    _no_color(monkeypatch)
    assert not any(item.key == "enter" for item in _ITEMS)
    frame = _render(Path("config.yml"), "1", cursor=0)
    assert "Enter-modes" not in frame


def test_selection_for_check_config() -> None:
    item = next(i for i in _ITEMS if i.key == "check")
    got = _selection_for(item, Path("config.yml"))
    assert got == MenuSelection(check_config=True, config=Path("config.yml"))


def test_selection_for_docs_needs_no_password(monkeypatch) -> None:
    called = False

    def _fail(_: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(menu, "_ensure_password", _fail)
    item = next(i for i in _ITEMS if i.key == "docs")
    got = _selection_for(item, Path("config.yml"))
    assert got == MenuSelection(mode="docs", config=Path("config.yml"))
    assert not called  # docs is offline, so it never asks for a secret


def test_selection_for_audit_is_a_discovery_that_opens_the_tree(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_ensure_password", lambda _: None)
    monkeypatch.setattr(menu.sys.stdout, "write", lambda _: None)
    item = next(i for i in _ITEMS if i.key == "audit")
    got = _selection_for(item, Path("config.yml"))
    # Audit always maps the mode structure (enter_modes) and then browses.
    assert got == MenuSelection(
        mode="audit", enter_modes=True, config=Path("config.yml"),
        discover=True, browse_after=True,
    )


def test_interactive_menu_returns_none_without_a_tty(monkeypatch) -> None:
    monkeypatch.setattr(menu.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(menu.sys.stdout, "isatty", lambda: True)
    assert interactive_menu(Path("config.yml"), "1.0") is None


def test_ensure_password_prompts_only_when_unset(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "c.yml"
    cfg.write_text(yaml.safe_dump({"device": {"host": "h", "password_env": "PW_X"}}))
    monkeypatch.delenv("PW_X", raising=False)
    monkeypatch.setattr(menu.getpass, "getpass", lambda _: "hunter2", raising=False)
    menu._ensure_password(cfg)
    assert menu.os.environ["PW_X"] == "hunter2"
    # A second call with the value already present must not overwrite it.
    monkeypatch.setattr(
        menu.getpass, "getpass", lambda _: "changed", raising=False
    )
    menu._ensure_password(cfg)
    assert menu.os.environ["PW_X"] == "hunter2"
    monkeypatch.delenv("PW_X", raising=False)


def test_fallback_menu_chooses_by_number(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "3")
    got = _fallback_menu(Path("config.yml"), "1.0")
    assert got == MenuSelection(mode="docs", config=Path("config.yml"))


def test_fallback_menu_cancels_on_blank(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _fallback_menu(Path("config.yml"), "1.0") is None


def test_target_meta_fills_transport_default_port(tmp_path: Path) -> None:
    ssh = _write_cfg(tmp_path / "s.yml", {"host": "h", "username": "op"})
    tel = _write_cfg(
        tmp_path / "t.yml", {"host": "h2", "transport": "telnet", "port": 2004}
    )
    assert _target_meta(ssh) == {
        "user": "op",
        "host": "h",
        "transport": "ssh",
        "port": 22,
    }
    telnet = _target_meta(tel)
    assert telnet["transport"] == "telnet" and telnet["port"] == 2004


def test_target_meta_is_none_without_a_device(tmp_path: Path) -> None:
    assert _target_meta(tmp_path / "gone.yml") is None
    plain = tmp_path / "notes.yml"
    plain.write_text(yaml.safe_dump({"something": "else"}))
    assert _target_meta(plain) is None


def test_target_label_formats_user_host_transport(tmp_path: Path) -> None:
    cfg = _write_cfg(
        tmp_path / "c.yml", {"host": "10.0.0.1", "username": "op", "port": 22}
    )
    assert _target_label(cfg) == "op@10.0.0.1  ssh:22"


def test_discover_targets_lists_only_device_configs(tmp_path: Path) -> None:
    _write_cfg(tmp_path / "a.yml", {"host": "h1", "username": "u"})
    _write_cfg(tmp_path / "b.yaml", {"host": "h2"})
    (tmp_path / "unrelated.yml").write_text(yaml.safe_dump({"nope": 1}))
    (tmp_path / "readme.txt").write_text("not yaml")
    found = {p.name for p in _discover_targets(tmp_path / "a.yml", base=tmp_path)}
    assert found == {"a.yml", "b.yaml"}


def test_discover_targets_reads_a_configs_subfolder(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    _write_cfg(tmp_path / "configs" / "lab.yml", {"host": "lab", "username": "u"})
    names = {p.name for p in _discover_targets(tmp_path / "missing.yml", base=tmp_path)}
    assert "lab.yml" in names


def test_prompt_return_continues_on_enter(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert menu.prompt_return() is True


def test_prompt_return_leaves_on_interrupt(monkeypatch) -> None:
    def _interrupt(_: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    assert menu.prompt_return() is False


def test_render_picker_lists_targets_and_manual_row(tmp_path: Path, monkeypatch) -> None:
    _no_color(monkeypatch)
    cfg = _write_cfg(tmp_path / "a.yml", {"host": "10.0.0.1", "username": "op"})
    targets = _discover_targets(cfg, base=tmp_path)
    frame = _render_picker(targets, cfg, cursor=0, version="1.0")
    assert "op@10.0.0.1  ssh:22" in frame
    assert "a.yml" in frame
    assert "Enter a path" in frame
    # The cursor marks exactly one row.
    assert len([ln for ln in frame.splitlines() if "▸" in ln]) == 1


# --------------------------------------------------------------------------- #
# Map browser
# --------------------------------------------------------------------------- #

def _write_catalog(path: Path, contexts: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"scan": {"contexts": contexts}}))
    return path


_MAP_CONTEXTS = [
    {"name": "root", "fingerprint": "#", "commands": 800, "queries": 800},
    {"name": "root/monitor", "fingerprint": ">", "commands": 10, "queries": 12,
     "entry_path": ["monitor"]},
    {"name": "root/system-view", "fingerprint": "(config)#", "commands": 30,
     "queries": 40, "entry_path": ["system-view"]},
    {"name": "root/system-view/vrf", "fingerprint": "(config-vrf)#",
     "commands": 20, "queries": 25, "entry_path": ["system-view", "ip vrf red"]},
]


def test_load_context_map_reads_scan_contexts(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "cli_real.yml", _MAP_CONTEXTS)
    root = load_context_map(catalog)
    assert root is not None
    assert root.name == "root"
    assert {c.label for c in root.children} == {"monitor", "system-view"}


def test_load_context_map_none_when_absent_or_empty(tmp_path: Path) -> None:
    assert load_context_map(tmp_path / "missing.yml") is None
    empty = tmp_path / "empty.yml"
    empty.write_text(yaml.safe_dump({"scan": {"contexts": []}}))
    assert load_context_map(empty) is None


def test_flatten_map_groups_exec_and_config(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    keys = [row.key for row in rows]
    assert keys[0] == _EXEC_KEY
    assert _CONFIG_KEY in keys
    # Both headers are open: monitor sits under exec, system-view under config;
    # vrf stays hidden until system-view itself is expanded.
    assert "root/monitor" in keys
    assert "root/system-view" in keys
    assert "root/system-view/vrf" not in keys


def test_flatten_map_expands_a_config_submode(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY, "root/system-view"})
    assert "root/system-view/vrf" in [row.key for row in rows]


def test_flatten_map_collapsed_headers_hide_children(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", set())  # nothing expanded
    assert [row.key for row in rows] == [_EXEC_KEY, _CONFIG_KEY]


def test_flatten_row_target_carries_entry_path(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY, "root/system-view"})
    vrf = next(row for row in rows if row.key == "root/system-view/vrf")
    assert vrf.target == RunTarget(
        "vrf",
        (ContextRef("root/system-view/vrf", "(config-vrf)#",
                    ("system-view", "ip vrf red")),),
        descend=True,
    )


def test_exec_header_runs_root_without_descending(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    exec_row = next(row for row in rows if row.key == _EXEC_KEY)
    assert exec_row.target.descend is False
    assert [ref.name for ref in exec_row.target.starts] == ["root"]


def test_config_header_runs_every_config_submode(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    config_row = next(row for row in rows if row.key == _CONFIG_KEY)
    assert config_row.target.descend is True
    assert [ref.name for ref in config_row.target.starts] == ["root/system-view"]


def test_tree_marks_fully_parsed_blocks_with_a_check(tmp_path: Path, monkeypatch) -> None:
    _no_color(monkeypatch)
    contexts = [
        {"name": "root", "fingerprint": "#", "commands": 10, "queries": 10},
        {"name": "root/system-view", "fingerprint": "(config)#",
         "entry_path": ["system-view"], "commands": 5, "queries": 5,
         "complete": True},   # deep-parsed block
        {"name": "root/monitor", "fingerprint": ">",
         "entry_path": ["monitor"], "commands": 2, "queries": 2,
         "complete": False},  # discovery-only block
    ]
    root = load_context_map(_write_catalog(tmp_path / "c.yml", contexts))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    frame = _render_map(root, "c.yml", "ssh", rows, cursor=0)
    system_line = next(l for l in frame.splitlines() if "system-view" in l)
    monitor_line = next(l for l in frame.splitlines() if "monitor" in l)
    assert "✓" in system_line
    assert "✓" not in monitor_line
    # The check must not bend the border.
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


def test_render_map_shows_badges_and_stays_aligned(tmp_path: Path, monkeypatch) -> None:
    _no_color(monkeypatch)
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY, "root/system-view"})
    frame = _render_map(root, "c.yml", "ssh", rows, cursor=0)
    assert "Exec mode" in frame and "Config mode" in frame
    assert "~" in frame  # at least one ETA badge
    # Every line is the same display width, so the border is straight.
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


def test_catalog_and_transport_reads_output_and_transport(tmp_path: Path) -> None:
    cfg = tmp_path / "dev.yml"
    cfg.write_text(yaml.safe_dump({
        "device": {"host": "10.0.0.1", "transport": "telnet"},
        "output": {"device_catalog": "output/custom_real.yml"},
    }))
    catalog, transport = _catalog_and_transport(cfg)
    assert catalog == Path("output/custom_real.yml")
    assert transport == "telnet"


def test_catalog_and_transport_falls_back_to_defaults(tmp_path: Path) -> None:
    catalog, transport = _catalog_and_transport(tmp_path / "missing.yml")
    assert catalog == Path("output/cli_real.yml")
    assert transport == "ssh"


def test_browse_map_returns_none_without_a_map(tmp_path: Path) -> None:
    # No catalog on disk -> nothing to browse, even on a tty.
    assert browse_map(tmp_path / "none.yml", version="1.0") is None


# --------------------------------------------------------------------------- #
# Language and theme
# --------------------------------------------------------------------------- #

def test_translate_falls_back_to_english_then_key() -> None:
    assert menu.t("audit_title") == "Audit device"
    menu._PREFS["lang"] = "ru"
    assert menu.t("audit_title") == "Аудит устройства"
    # An id with no translation degrades to the id itself, never a crash.
    assert menu.t("no_such_key_here") == "no_such_key_here"


def test_cycle_pref_flips_language_and_theme() -> None:
    assert menu._PREFS["lang"] == "en"
    menu._cycle_pref("lang")
    assert menu._PREFS["lang"] == "ru"
    menu._cycle_pref("lang")
    assert menu._PREFS["lang"] == "en"
    menu._cycle_pref("theme")
    assert menu._PREFS["theme"] == "light"


def test_render_switches_to_russian(monkeypatch) -> None:
    _no_color(monkeypatch)
    menu._PREFS["lang"] = "ru"
    frame = _render(Path("sw.yml"), "0.6.0", cursor=0)
    assert "Аудит устройства" in frame
    assert "Audit device" not in frame
    # The language row shows the active code.
    assert "RU" in frame


def test_render_shows_language_and_theme_rows(monkeypatch) -> None:
    _no_color(monkeypatch)
    frame = _render(Path("sw.yml"), "0.6.0", cursor=0)
    assert "Language" in frame and "Theme" in frame
    assert "EN" in frame and "dark" in frame


def test_theme_changes_the_selection_colour(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_use_color", lambda: True)
    # With colour on, the selected row carries the theme's SGR codes.
    menu._PREFS["theme"] = "dark"
    dark = menu._render(Path("sw.yml"), "0.6.0", cursor=0)
    menu._PREFS["theme"] = "light"
    light = menu._render(Path("sw.yml"), "0.6.0", cursor=0)
    assert "\x1b[30;46m" in dark  # black-on-cyan selection
    assert "\x1b[97;44m" in light  # white-on-blue selection
    assert dark != light


def test_russian_launcher_border_stays_aligned(monkeypatch) -> None:
    _no_color(monkeypatch)
    menu._PREFS["lang"] = "ru"
    frame = _render(Path("устройство.yml"), "0.6.0", cursor=2)
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


# --------------------------------------------------------------------------- #
# Device setup and connection gating
# --------------------------------------------------------------------------- #

def test_gated_runs_are_locked_until_connected(monkeypatch) -> None:
    _no_color(monkeypatch)
    locked = _render(Path("c.yml"), "1.0", cursor=0, connected=False)
    audit_row = next(l for l in locked.splitlines() if "Audit device" in l)
    assert "⊘" in audit_row
    assert "set up the device first" in audit_row

    unlocked = _render(
        Path("c.yml"), "1.0", cursor=0, connected=True, connected_to="op@10.0.0.1"
    )
    audit_row = next(l for l in unlocked.splitlines() if "Audit device" in l)
    assert "⊘" not in audit_row
    assert "inventory over SSH" in audit_row


def test_connected_status_line_shows_the_device(monkeypatch) -> None:
    _no_color(monkeypatch)
    frame = _render(
        Path("c.yml"), "1.0", cursor=0, connected=True, connected_to="admin@sw1"
    )
    status = next(l for l in frame.splitlines() if "connected" in l)
    assert "admin@sw1" in status
    assert "✓" in status


def test_only_audit_and_compare_are_gated() -> None:
    assert _GATED_KEYS == frozenset({"audit", "compare"})


def test_render_setup_lists_fields_and_stays_aligned(monkeypatch, tmp_path) -> None:
    _no_color(monkeypatch)
    fields = {"host": "10.0.0.1", "port": 22, "username": "op",
              "transport": "ssh", "password_env": "SWITCH_PASSWORD"}
    frame = _render_setup(tmp_path / "c.yml", fields, cursor=0,
                          message="", message_role="dim")
    assert "Host" in frame and "10.0.0.1" in frame
    assert "Save & test connection" in frame
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


def test_password_is_set_reads_the_named_env(monkeypatch) -> None:
    fields = {"password_env": "MY_SECRET"}
    monkeypatch.delenv("MY_SECRET", raising=False)
    assert _password_is_set(fields) is False
    monkeypatch.setenv("MY_SECRET", "x")
    assert _password_is_set(fields) is True


def test_edit_field_cycles_transport_and_follows_the_port(tmp_path) -> None:
    fields = {"host": "h", "transport": "ssh", "port": 22}
    _edit_field(tmp_path / "c.yml", fields, "transport")
    assert fields["transport"] == "telnet"
    assert fields["port"] == 23  # followed the conventional port
    _edit_field(tmp_path / "c.yml", fields, "transport")
    assert fields["transport"] == "ssh"
    assert fields["port"] == 22


def test_edit_field_keeps_a_custom_port_when_switching(tmp_path) -> None:
    fields = {"host": "h", "transport": "ssh", "port": 2222}  # non-conventional
    _edit_field(tmp_path / "c.yml", fields, "transport")
    assert fields["transport"] == "telnet"
    assert fields["port"] == 2222  # a deliberate port is left untouched


# --------------------------------------------------------------------------- #
# Host-key trust step
# --------------------------------------------------------------------------- #

_FAKE_KEY = {"type": "ssh-ed25519", "base64": "AAAAfake",
             "fingerprint": "SHA256:abc"}


def test_ensure_host_key_accepts_and_pins_on_y(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(menu, "fetch_host_key", lambda h, p: (_FAKE_KEY, ""))
    monkeypatch.setattr(menu, "_notice", lambda lines, wait=True: None)
    monkeypatch.setattr(menu, "_read_key", lambda: "y")
    cfg = tmp_path / "config.yml"
    cfg.write_text("")
    fields = {"host": "10.0.0.1", "port": 22, "username": "op",
              "transport": "ssh", "password_env": "SWITCH_PASSWORD"}
    ok, note = menu._ensure_host_key(cfg, fields, "10.0.0.1", 22)
    assert ok is True and note == ""
    known = tmp_path / "known_hosts"
    assert "10.0.0.1 ssh-ed25519 AAAAfake" in known.read_text()
    # The config now records where the pinned key lives.
    data = yaml.safe_load(cfg.read_text())
    assert data["device"]["known_hosts"] == str(known)


def test_ensure_host_key_rejects_on_anything_else(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(menu, "fetch_host_key", lambda h, p: (_FAKE_KEY, ""))
    monkeypatch.setattr(menu, "_notice", lambda lines, wait=True: None)
    monkeypatch.setattr(menu, "_read_key", lambda: "n")
    cfg = tmp_path / "config.yml"
    cfg.write_text("")
    ok, _note = menu._ensure_host_key(cfg, {}, "10.0.0.1", 22)
    assert ok is False
    assert not (tmp_path / "known_hosts").exists()  # nothing was written


def test_ensure_host_key_skips_the_question_when_already_pinned(
    tmp_path, monkeypatch
) -> None:
    from cliradar.devicecfg import pin_host_key

    known = tmp_path / "known_hosts"
    pin_host_key(known, "10.0.0.1", 22, _FAKE_KEY)
    monkeypatch.setattr(
        menu, "fetch_host_key",
        lambda h, p: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    cfg = tmp_path / "config.yml"
    cfg.write_text("")
    fields = {"known_hosts": str(known)}
    ok, _note = menu._ensure_host_key(cfg, fields, "10.0.0.1", 22)
    assert ok is True


def test_ensure_host_key_reports_fetch_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(menu, "fetch_host_key", lambda h, p: (None, "timed out"))
    cfg = tmp_path / "config.yml"
    cfg.write_text("")
    ok, note = menu._ensure_host_key(cfg, {}, "10.0.0.1", 22)
    assert ok is False
    assert "timed out" in note


# --------------------------------------------------------------------------- #
# Docs folder
# --------------------------------------------------------------------------- #

def test_open_docs_folder_creates_dir_and_spells_out_the_format(
    tmp_path, monkeypatch
) -> None:
    _no_color(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(menu, "_notice", lambda lines: captured.setdefault("lines", lines))
    monkeypatch.setattr(menu, "_launch_file_manager", lambda p: True)
    docs = tmp_path / "vendor_docs"
    menu._open_docs_folder(docs)
    assert docs.is_dir()  # the folder is created if missing
    text = "\n".join(captured["lines"])
    # The note lists exactly the formats the parser really reads.
    assert ".txt" in text and ".md" in text and ".rst" in text
    assert ".doc" not in text
    assert "vendor_docs" in text


def test_launch_file_manager_uses_an_available_opener(monkeypatch) -> None:
    import shutil
    import subprocess
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/bin/xdg-open" if name == "xdg-open" else None)
    launched: dict = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: launched.setdefault("argv", a[0]))
    assert menu._launch_file_manager(Path("/tmp/x")) is True
    assert launched["argv"][0] == "xdg-open"


def test_launch_file_manager_reports_when_none_present(monkeypatch) -> None:
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert menu._launch_file_manager(Path("/tmp/x")) is False


# --------------------------------------------------------------------------- #
# Crash guard: a failure becomes a panel, never a drop to the shell
# --------------------------------------------------------------------------- #

def _quiet_screen(monkeypatch) -> None:
    """Silence the terminal writes so a menu loop can be driven in a test."""
    monkeypatch.setattr(menu.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(menu.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu.sys.stdout, "write", lambda _: None)
    monkeypatch.setattr(menu.sys.stdout, "flush", lambda: None)


def _keys(monkeypatch, *presses: str) -> None:
    """Feed a fixed sequence of keypresses; the last one repeats forever."""
    seq = list(presses)

    def press() -> str:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(menu, "_read_key", press)


def test_record_crash_writes_the_traceback(tmp_path, monkeypatch) -> None:
    log = tmp_path / "logs" / "menu-errors.log"
    monkeypatch.setattr(menu, "CRASH_LOG", log)
    try:
        raise ValueError("kaboom")
    except ValueError as exc:
        assert menu._record_crash(exc, "setup · save") == log
    text = log.read_text(encoding="utf-8")
    assert "ValueError: kaboom" in text
    assert "[setup · save]" in text
    assert "Traceback" in text


def test_record_crash_survives_an_unwritable_log(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(menu, "CRASH_LOG", blocker / "menu-errors.log")
    try:
        raise ValueError("kaboom")
    except ValueError as exc:
        assert menu._record_crash(exc, "menu") is None


def test_crash_lines_name_the_fault_its_place_and_the_log(monkeypatch) -> None:
    _no_color(monkeypatch)
    try:
        raise RuntimeError("host key vanished")
    except RuntimeError as exc:
        lines = menu._crash_lines(exc, "setup · save", Path("output/menu-errors.log"))
    text = "\n".join(lines)
    assert "Something went wrong" in text
    assert "RuntimeError: host key vanished" in text
    assert "setup · save" in text
    # The tail of the traceback points at this test's own frame.
    assert "test_menu.py" in text
    assert "output/menu-errors.log" in text
    # Nothing overflows the panel's inner width.
    assert max(menu._disp_width(line) for line in lines) <= menu._WIDTH


def test_interactive_menu_survives_a_failing_screen(tmp_path, monkeypatch) -> None:
    """A raised error redraws the launcher instead of ending the session."""
    _quiet_screen(monkeypatch)
    _keys(monkeypatch, "q")
    caught: list = []
    monkeypatch.setattr(menu, "_crash_notice", lambda exc, where: caught.append((exc, where)))
    draws = {"n": 0}

    def render(*args, **kwargs) -> str:
        draws["n"] += 1
        if draws["n"] == 1:
            raise RuntimeError("panel exploded")
        return "frame"

    monkeypatch.setattr(menu, "_render", render)
    assert interactive_menu(tmp_path / "c.yml", "9.9.9") is None
    assert draws["n"] == 2  # it came back and drew again
    assert "panel exploded" in str(caught[0][0])
    assert caught[0][1].startswith("menu · ")


def test_interactive_menu_gives_up_after_repeated_failures(
    tmp_path, monkeypatch
) -> None:
    """A terminal that keeps failing must end, not spin drawing panels."""
    _quiet_screen(monkeypatch)
    _keys(monkeypatch, "q")
    monkeypatch.setattr(menu, "_crash_notice", lambda exc, where: None)
    draws = {"n": 0}

    def always_fails(*args, **kwargs) -> str:
        draws["n"] += 1
        raise RuntimeError("terminal is gone")

    monkeypatch.setattr(menu, "_render", always_fails)
    with pytest.raises(RuntimeError):
        interactive_menu(tmp_path / "c.yml", "9.9.9")
    assert draws["n"] == menu._CRASH_LIMIT


def test_setup_screen_shows_a_failed_save_instead_of_exiting(
    tmp_path, monkeypatch
) -> None:
    """The reported "validate kicks me out" path: the form stays and explains."""
    _quiet_screen(monkeypatch)
    monkeypatch.setattr(
        menu, "load_device_fields",
        lambda path: {"host": "10.0.0.1", "username": "op",
                      "transport": "ssh", "port": 22},
    )

    def unwritable(*args, **kwargs) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(menu, "save_device_fields", unwritable)
    # A reach test would touch the network; the save fails long before it.
    monkeypatch.setattr(menu, "probe_reachable",
                        lambda *a, **k: pytest.fail("must not reach the device"))
    monkeypatch.setattr(menu, "_crash_notice", lambda exc, where: None)
    frames: list[tuple[str, str]] = []

    def render_setup(config_path, fields, cursor, message, role) -> str:
        frames.append((message, role))
        return "frame"

    monkeypatch.setattr(menu, "_render_setup", render_setup)
    # Walk down to "Save & test", press it, then leave.
    _keys(monkeypatch, *(["down"] * 5 + ["enter", "q"]))
    connected, where = menu._setup_screen(tmp_path / "c.yml", False, "")
    assert connected is False and where == ""
    message, role = frames[-1]
    assert "read-only file system" in message
    assert role == "bad"


def test_render_setup_trims_a_long_message_to_the_panel(monkeypatch) -> None:
    _no_color(monkeypatch)
    frame = _render_setup(
        Path("c.yml"), {"host": "10.0.0.1"}, 0, "E" * 200, "bad"
    )
    widths = {menu._disp_width(line) for line in frame.splitlines()}
    assert len(widths) == 1  # every row, border included, is the same width


# --------------------------------------------------------------------------- #
# Memory between starts, and the "what did I find to read" docs panel
# --------------------------------------------------------------------------- #

def _fresh_prefs(monkeypatch) -> None:
    """Start each memory test from the shipped defaults, not a leaked state."""
    monkeypatch.setitem(menu._PREFS, "lang", "en")
    monkeypatch.setitem(menu._PREFS, "theme", "dark")
    monkeypatch.setitem(menu._PREFS, "config", "")
    monkeypatch.setitem(menu._PREFS, "runs", [])


def test_theme_and_language_survive_a_restart(monkeypatch) -> None:
    _fresh_prefs(monkeypatch)
    menu._cycle_pref("lang")
    menu._cycle_pref("theme")
    _fresh_prefs(monkeypatch)  # as if the process had exited and come back
    menu._restore_prefs()
    assert menu._PREFS["lang"] == "ru"
    assert menu._PREFS["theme"] == "light"


def test_remembered_target_only_fills_a_missing_path(tmp_path, monkeypatch) -> None:
    _fresh_prefs(monkeypatch)
    saved = _write_cfg(tmp_path / "saved.yml", {"host": "10.0.0.9"})
    menu._remember_target(saved)
    # A path that exists wins over the memory ...
    here = _write_cfg(tmp_path / "here.yml", {"host": "10.0.0.1"})
    assert menu._remembered_target(here) == here
    # ... and the memory fills in for one that does not.
    assert menu._remembered_target(tmp_path / "gone.yml") == saved.resolve()


def test_remembered_target_ignores_a_deleted_config(tmp_path, monkeypatch) -> None:
    _fresh_prefs(monkeypatch)
    gone = tmp_path / "gone.yml"
    monkeypatch.setitem(menu._PREFS, "config", str(gone))
    asked = tmp_path / "asked.yml"
    assert menu._remembered_target(asked) == asked


def test_launched_runs_are_remembered_and_shown(monkeypatch) -> None:
    _no_color(monkeypatch)
    _fresh_prefs(monkeypatch)
    item = next(i for i in _ITEMS if i.key == "check")
    _selection_for(item, Path("config.yml"))
    assert menu._PREFS["runs"][0]["mode"] == "check"
    frame = _render(Path("config.yml"), "1.0", cursor=0)
    assert "last run" in frame
    assert "Validate config" in frame.split("last run")[1]


def test_launcher_without_history_shows_no_last_run_line(monkeypatch) -> None:
    _no_color(monkeypatch)
    _fresh_prefs(monkeypatch)
    assert "last run" not in _render(Path("config.yml"), "1.0", cursor=0)


def test_run_history_is_written_to_the_state_file(monkeypatch) -> None:
    _fresh_prefs(monkeypatch)
    menu._remember_run("audit")
    from cliradar.prefs import load_prefs

    assert load_prefs()["runs"][0]["mode"] == "audit"


def test_list_doc_files_finds_manuals_recursively(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub" / "b.md").write_text("x")
    (tmp_path / "ignored.pdf").write_text("x")
    assert menu.list_doc_files(tmp_path) == ["a.txt", "sub/b.md"]


def test_list_doc_files_on_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert menu.list_doc_files(tmp_path / "nope") == []


def test_docs_panel_names_what_it_found(tmp_path, monkeypatch) -> None:
    _no_color(monkeypatch)
    (tmp_path / "vrf.txt").write_text("x")
    (tmp_path / "vlan.md").write_text("x")
    text = "\n".join(menu._docs_found_lines(tmp_path))
    assert "found (2)" in text
    assert "vlan.md" in text and "vrf.txt" in text


def test_docs_panel_collapses_a_long_list(tmp_path, monkeypatch) -> None:
    _no_color(monkeypatch)
    for index in range(menu._DOCS_LISTED + 3):
        (tmp_path / f"doc{index}.txt").write_text("x")
    text = "\n".join(menu._docs_found_lines(tmp_path))
    assert "and 3 more" in text


def test_docs_panel_says_the_folder_is_empty(tmp_path, monkeypatch) -> None:
    _no_color(monkeypatch)
    text = "\n".join(menu._docs_found_lines(tmp_path))
    assert "nothing to read here yet" in text


# --------------------------------------------------------------------------- #
# The map browser: node states and the blueprint of expected blocks
# --------------------------------------------------------------------------- #

def test_tree_marks_each_node_with_its_state(tmp_path: Path, monkeypatch) -> None:
    _no_color(monkeypatch)
    contexts = [
        {"name": "root", "fingerprint": "#", "commands": 10, "queries": 10},
        {"name": "root/system-view", "fingerprint": "(config)#",
         "entry_path": ["system-view"], "commands": 5, "queries": 5,
         "complete": True},                              # ✓ parsed
        {"name": "root/monitor", "fingerprint": ">",
         "entry_path": ["monitor"], "commands": 2, "queries": 2},   # ⊙ topped
        {"name": "root/diagnose", "fingerprint": ">",
         "entry_path": ["diagnose"], "commands": 0, "queries": 0},  # ○ unknown
    ]
    root = load_context_map(_write_catalog(tmp_path / "c.yml", contexts))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    frame = _render_map(root, "c.yml", "ssh", rows, cursor=0)
    line_of = lambda name: next(l for l in frame.splitlines() if name in l)
    assert "✓" in line_of("system-view")
    assert "⊙" in line_of("monitor")
    assert "○" in line_of("diagnose")
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


def test_blueprint_fills_in_the_blocks_the_map_lacks(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    labels = [row.label for row in rows if row.key.startswith("blueprint/")]
    # The tree has shape before anything has been scanned into it.
    assert "vlan" in labels and "vrf" in labels and "mlag" in labels


def test_a_blueprint_row_runs_only_its_own_block(tmp_path: Path) -> None:
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    vlan = next(row for row in rows if row.key == "blueprint/vlan")
    assert vlan.target is not None
    assert vlan.target.focus_verbs == ("vlan",)
    # It starts from the proven config context, not from thin air.
    assert [ref.name for ref in vlan.target.starts] == ["root/system-view"]


def test_a_refused_block_is_shown_as_absent_and_is_not_runnable(
    tmp_path: Path, monkeypatch
) -> None:
    _no_color(monkeypatch)
    root = load_context_map(_write_catalog(tmp_path / "c.yml", _MAP_CONTEXTS))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY}, {"mlag"})
    mlag = next(row for row in rows if row.key == "blueprint/mlag")
    assert mlag.state == "absent"
    assert mlag.target is None  # nothing to scan: the device said it is not there
    frame = _render_map(root, "c.yml", "ssh", rows, cursor=0)
    assert "not on this device" in frame


def test_blueprint_rows_are_drawn_but_idle_without_a_config_context(
    tmp_path: Path,
) -> None:
    contexts = [{"name": "root", "fingerprint": "#", "commands": 4, "queries": 4}]
    root = load_context_map(_write_catalog(tmp_path / "c.yml", contexts))
    rows = _flatten_map(root, "ssh", {_EXEC_KEY, _CONFIG_KEY})
    blueprint = [row for row in rows if row.key.startswith("blueprint/")]
    assert blueprint  # the shape is still shown ...
    # ... but there is no proven context to start from, so nothing is launched.
    assert all(row.target is None for row in blueprint)


def test_load_rejected_verbs_reads_the_probes(tmp_path: Path) -> None:
    path = tmp_path / "c.yml"
    path.write_text(yaml.safe_dump({
        "scan": {
            "contexts": _MAP_CONTEXTS,
            "probes": [
                {"command": "mlag", "outcome": "rejected"},
                {"command": "vlan 1", "outcome": "entered"},
            ],
        }
    }))
    assert menu.load_rejected_verbs(path) == {"mlag"}


def test_load_rejected_verbs_survives_a_broken_catalog(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yml"
    broken.write_text("scan: [not, a, mapping")
    assert menu.load_rejected_verbs(broken) == set()


# --------------------------------------------------------------------------- #
# The run banner and the progress line the launcher owns
# --------------------------------------------------------------------------- #

def test_run_banner_states_the_run_and_the_way_out(tmp_path, monkeypatch) -> None:
    _no_color(monkeypatch)
    cfg = _write_cfg(tmp_path / "sw.yml", {"host": "10.0.0.1", "username": "op"})
    banner = menu.run_banner(cfg, "audit")
    assert "Audit device" in banner
    assert "10.0.0.1" in banner
    assert "Ctrl-C" in banner  # the run is cancellable and says so
    assert len({_disp_width(line) for line in banner.splitlines()}) == 1


def test_stage_labels_follow_the_menu_language(monkeypatch) -> None:
    monkeypatch.setitem(menu._PREFS, "lang", "en")
    assert menu.stage_label("crawl") == "queries"
    monkeypatch.setitem(menu._PREFS, "lang", "ru")
    assert menu.stage_label("crawl") == "запросов"
    # An unknown stage falls back to its own name rather than a missing key.
    assert menu.stage_label("something-new") == "something-new"
