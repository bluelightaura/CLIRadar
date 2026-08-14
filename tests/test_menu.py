from pathlib import Path

import pytest
import yaml

from cliradar import menu
from cliradar.menu import (
    _CONFIG_KEY,
    _EXEC_KEY,
    _ITEMS,
    ContextRef,
    MenuSelection,
    RunTarget,
    _GATED_KEYS,
    _catalog_and_transport,
    _discover_targets,
    _edit_field,
    _password_is_set,
    _render_setup,
    _disp_width,
    _fallback_menu,
    _flatten_map,
    _render,
    _render_map,
    _render_picker,
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
    frame = _render(Path("config.yml"), "9.9.9", cursor=2, enter_on=False)
    assert "CLIRadar 9.9.9" in frame
    for item in _ITEMS:
        assert item.title in frame
    # Only the cursor row carries the pointer.
    pointed = [line for line in frame.splitlines() if "▸" in line]
    assert len(pointed) == 1
    assert "Compare vs docs" in pointed[0]


def test_render_toggle_state_switches(monkeypatch) -> None:
    _no_color(monkeypatch)
    assert "[off]" in _render(Path("config.yml"), "1", cursor=0, enter_on=False)
    assert "[on]" in _render(Path("config.yml"), "1", cursor=0, enter_on=True)


def test_selection_for_check_config() -> None:
    item = next(i for i in _ITEMS if i.key == "check")
    got = _selection_for(item, Path("config.yml"), enter_on=False)
    assert got == MenuSelection(check_config=True, config=Path("config.yml"))


def test_selection_for_docs_needs_no_password(monkeypatch) -> None:
    called = False

    def _fail(_: Path) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(menu, "_ensure_password", _fail)
    item = next(i for i in _ITEMS if i.key == "docs")
    got = _selection_for(item, Path("config.yml"), enter_on=False)
    assert got == MenuSelection(mode="docs", config=Path("config.yml"))
    assert not called  # docs is offline, so it never asks for a secret


def test_selection_for_audit_is_a_discovery_that_opens_the_tree(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_ensure_password", lambda _: None)
    monkeypatch.setattr(menu.sys.stdout, "write", lambda _: None)
    item = next(i for i in _ITEMS if i.key == "audit")
    got = _selection_for(item, Path("config.yml"), enter_on=False)
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
    frame = _render(Path("sw.yml"), "0.6.0", cursor=0, enter_on=False)
    assert "Аудит устройства" in frame
    assert "Audit device" not in frame
    # The language row shows the active code.
    assert "RU" in frame


def test_render_shows_language_and_theme_rows(monkeypatch) -> None:
    _no_color(monkeypatch)
    frame = _render(Path("sw.yml"), "0.6.0", cursor=0, enter_on=False)
    assert "Language" in frame and "Theme" in frame
    assert "EN" in frame and "dark" in frame


def test_theme_changes_the_selection_colour(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_use_color", lambda: True)
    # With colour on, the selected row carries the theme's SGR codes.
    menu._PREFS["theme"] = "dark"
    dark = menu._render(Path("sw.yml"), "0.6.0", cursor=0, enter_on=False)
    menu._PREFS["theme"] = "light"
    light = menu._render(Path("sw.yml"), "0.6.0", cursor=0, enter_on=False)
    assert "\x1b[30;46m" in dark  # black-on-cyan selection
    assert "\x1b[97;44m" in light  # white-on-blue selection
    assert dark != light


def test_russian_launcher_border_stays_aligned(monkeypatch) -> None:
    _no_color(monkeypatch)
    menu._PREFS["lang"] = "ru"
    frame = _render(Path("устройство.yml"), "0.6.0", cursor=2, enter_on=True)
    assert len({_disp_width(line) for line in frame.splitlines()}) == 1


# --------------------------------------------------------------------------- #
# Device setup and connection gating
# --------------------------------------------------------------------------- #

def test_gated_runs_are_locked_until_connected(monkeypatch) -> None:
    _no_color(monkeypatch)
    locked = _render(Path("c.yml"), "1.0", cursor=0, enter_on=False,
                     connected=False)
    audit_row = next(l for l in locked.splitlines() if "Audit device" in l)
    assert "⊘" in audit_row
    assert "set up the device first" in audit_row

    unlocked = _render(Path("c.yml"), "1.0", cursor=0, enter_on=False,
                       connected=True, connected_to="op@10.0.0.1")
    audit_row = next(l for l in unlocked.splitlines() if "Audit device" in l)
    assert "⊘" not in audit_row
    assert "inventory over SSH" in audit_row


def test_connected_status_line_shows_the_device(monkeypatch) -> None:
    _no_color(monkeypatch)
    frame = _render(Path("c.yml"), "1.0", cursor=0, enter_on=False,
                    connected=True, connected_to="admin@sw1")
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
