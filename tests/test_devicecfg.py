"""Tests for the device-config read/write and reach-test helpers."""

from __future__ import annotations

import socket
from pathlib import Path

import yaml

from cliradar.devicecfg import (
    load_device_fields,
    probe_reachable,
    save_device_fields,
)


def test_load_defaults_when_file_is_missing(tmp_path: Path) -> None:
    fields = load_device_fields(tmp_path / "nope.yml")
    assert fields["host"] == ""
    assert fields["port"] == 22
    assert fields["transport"] == "ssh"
    assert fields["password_env"] == "SWITCH_PASSWORD"


def test_load_reads_existing_device_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(yaml.safe_dump({
        "device": {"host": "10.0.0.1", "port": 23, "username": "op",
                   "transport": "telnet"},
    }))
    fields = load_device_fields(cfg)
    assert fields["host"] == "10.0.0.1"
    assert fields["port"] == 23
    assert fields["transport"] == "telnet"
    assert fields["username"] == "op"


def test_save_preserves_other_sections(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(yaml.safe_dump({
        "device": {"host": "old", "username": "old"},
        "discovery": {"max_depth": 7},
        "output": {"device_catalog": "output/cli_real.yml"},
    }))
    save_device_fields(cfg, {"host": "10.9.9.9", "username": "admin",
                             "port": 22, "transport": "ssh",
                             "password_env": "SWITCH_PASSWORD"})
    data = yaml.safe_load(cfg.read_text())
    assert data["device"]["host"] == "10.9.9.9"
    assert data["device"]["username"] == "admin"
    # Untouched sections survive the write.
    assert data["discovery"]["max_depth"] == 7
    assert data["output"]["device_catalog"] == "output/cli_real.yml"


def test_save_never_writes_a_password(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(yaml.safe_dump({"device": {"password": "leaked"}}))
    save_device_fields(cfg, {"host": "h", "username": "u", "port": 22,
                             "transport": "ssh", "password_env": "SWITCH_PASSWORD"})
    text = cfg.read_text()
    assert "leaked" not in text
    assert "password:" not in text  # only password_env is written
    assert "password_env: SWITCH_PASSWORD" in text


def test_save_creates_a_device_section_in_a_bare_file(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("")  # empty file
    save_device_fields(cfg, {"host": "h", "username": "u", "port": 22,
                             "transport": "ssh", "password_env": "SWITCH_PASSWORD"})
    data = yaml.safe_load(cfg.read_text())
    assert data["device"]["host"] == "h"


def test_probe_reaches_a_listening_socket() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        ticks: list[float] = []
        ok, note = probe_reachable("127.0.0.1", port, timeout=2.0,
                                   on_tick=ticks.append)
        assert ok is True
        assert note == "reachable"
        assert ticks and ticks[-1] == 1.0  # the bar finished
    finally:
        server.close()


def test_probe_fails_fast_on_a_closed_port() -> None:
    # Bind then close to obtain a port nothing listens on.
    scratch = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    scratch.bind(("127.0.0.1", 0))
    port = scratch.getsockname()[1]
    scratch.close()
    ok, note = probe_reachable("127.0.0.1", port, timeout=1.0)
    assert ok is False
    assert note  # a non-empty reason


def test_probe_reports_a_missing_host() -> None:
    ok, note = probe_reachable("", 22)
    assert ok is False
    assert note == "no host set"
