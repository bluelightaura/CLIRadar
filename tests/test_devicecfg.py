"""Tests for the device-config read/write and reach-test helpers."""

from __future__ import annotations

import socket
from pathlib import Path

import yaml

from cliradar.devicecfg import (
    fetch_host_key,
    host_key_is_pinned,
    load_device_fields,
    pin_host_key,
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


# --------------------------------------------------------------------------- #
# Host-key pinning
# --------------------------------------------------------------------------- #

_KEY = {"type": "ecdsa-sha2-nistp256", "base64": "AAAAtest", "fingerprint": "SHA256:x"}


def test_pin_creates_the_file_and_lookup_finds_it(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    assert host_key_is_pinned(path, "10.0.0.1", 22) is False
    pin_host_key(path, "10.0.0.1", 22, _KEY)
    assert host_key_is_pinned(path, "10.0.0.1", 22) is True
    assert path.read_text() == "10.0.0.1 ecdsa-sha2-nistp256 AAAAtest\n"


def test_pin_uses_bracketed_form_for_odd_ports(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    pin_host_key(path, "10.0.0.1", 2004, _KEY)
    assert "[10.0.0.1]:2004 " in path.read_text()
    assert host_key_is_pinned(path, "10.0.0.1", 2004) is True
    assert host_key_is_pinned(path, "10.0.0.1", 22) is False  # a different target


def test_repinning_replaces_the_old_key_not_duplicates(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    pin_host_key(path, "10.0.0.1", 22, _KEY)
    fresh = dict(_KEY, base64="AAAAnew")  # the device was reinstalled
    pin_host_key(path, "10.0.0.1", 22, fresh)
    text = path.read_text()
    assert text.count("10.0.0.1") == 1
    assert "AAAAnew" in text and "AAAAtest" not in text


def test_pin_keeps_other_hosts_entries(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    pin_host_key(path, "10.0.0.1", 22, _KEY)
    pin_host_key(path, "10.0.0.2", 22, _KEY)
    text = path.read_text()
    assert "10.0.0.1 " in text and "10.0.0.2 " in text


def test_save_persists_known_hosts_path(tmp_path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("")
    save_device_fields(cfg, {"host": "h", "username": "u", "port": 22,
                             "transport": "ssh", "password_env": "SWITCH_PASSWORD",
                             "known_hosts": str(tmp_path / "known_hosts")})
    data = yaml.safe_load(cfg.read_text())
    assert data["device"]["known_hosts"].endswith("known_hosts")


def test_fetch_host_key_reports_a_failure_reason(monkeypatch) -> None:
    import paramiko

    def boom(_addr):
        raise OSError("no route to host")

    monkeypatch.setattr(paramiko, "Transport", boom)
    key, note = fetch_host_key("203.0.113.1", 22)
    assert key is None
    assert "no route" in note


def test_fetch_host_key_returns_type_and_fingerprint(monkeypatch) -> None:
    import paramiko

    class FakeKey:
        def get_name(self):
            return "ssh-ed25519"

        def get_base64(self):
            return "AAAAfake"

        def asbytes(self):
            return b"raw-key-bytes"

    class FakeTransport:
        def __init__(self, _addr):
            pass

        def start_client(self, timeout=None):
            pass

        def get_remote_server_key(self):
            return FakeKey()

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "Transport", FakeTransport)
    key, note = fetch_host_key("203.0.113.1", 22)
    assert note == ""
    assert key["type"] == "ssh-ed25519"
    assert key["base64"] == "AAAAfake"
    assert key["fingerprint"].startswith("SHA256:")
