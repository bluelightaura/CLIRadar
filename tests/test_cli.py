import os
import stat
from pathlib import Path
from typing import Self

import pytest
import yaml
from fake_device import FakeDevice

from cliradar.cli import (
    ExitCode,
    main,
    merge_context_scan,
    run,
    write_catalog,
    write_html_report,
)
from cliradar.models import Catalog


def test_writes_private_yaml_catalog(tmp_path: Path) -> None:
    destination = tmp_path / "commands.yml"
    catalog = Catalog(device={"identity": "redacted"})
    catalog.add("show version", "Displays version", "documentation")

    write_catalog(catalog, destination)

    content = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert content["commands"][0]["command"] == "show version"
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(destination).st_mode) == 0o600


def test_context_commands_are_keyed_by_their_full_path() -> None:
    # A command that only exists inside a mode has to read as the sequence a
    # person would type, otherwise "name" from a VLAN view collides with any
    # other "name" in the catalog.
    class FakeScan:
        def __init__(self, entry_path: tuple[str, ...], catalog: Catalog) -> None:
            self.context = type("Ctx", (), {"entry_path": entry_path})()
            self.catalog = catalog

    inner = Catalog(device={})
    inner.add("name", "Set VLAN name", "cli").executable = True
    catalog = Catalog(device={})

    merge_context_scan(catalog, FakeScan(("configure", "vlan 1"), inner))

    assert "configure vlan 1 name" in catalog.commands
    assert catalog.commands["configure vlan 1 name"].executable is True


def test_refuses_symlink_catalog(tmp_path: Path) -> None:
    target = tmp_path / "target.yml"
    target.write_text("do not replace", encoding="utf-8")
    link = tmp_path / "commands.yml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("creating symlinks requires additional privileges on this platform")

    with pytest.raises(RuntimeError, match="symbolic link"):
        write_catalog(Catalog(device={}), link)

    assert target.read_text(encoding="utf-8") == "do not replace"


def test_catalog_redacts_device_identity(tmp_path: Path) -> None:
    destination = tmp_path / "commands.yml"
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True},
    )
    catalog.add("show version", "", "cli")
    catalog.add("show version", "", "documentation:commands.txt")

    write_catalog(catalog, destination)

    content = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert content["device"] == {"identity": "redacted"}
    assert content["mode"] == "compare"
    assert content["summary"]["matched"] == 1


def test_writes_private_html_report(tmp_path: Path) -> None:
    destination = tmp_path / "missing.html"
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="compare",
        scan={"complete": True},
    )
    catalog.add("show missing", "", "documentation:commands.txt")

    write_html_report(catalog, destination)

    content = destination.read_text(encoding="utf-8")
    assert "<!doctype html>" in content
    assert "<code>show missing</code>" in content
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(destination).st_mode) == 0o600


def test_audit_marks_every_device_command_as_present() -> None:
    catalog = Catalog(
        device={"identity": "redacted"},
        mode="audit",
        scan={"complete": True},
    )
    catalog.add("show", "Show information", "cli")

    content = catalog.to_dict()

    assert content["commands"][0]["device_status"] == "present"
    assert content["summary"] == {"device_commands": 1, "present": 1}


def test_prints_version(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["cliradar", "--version"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == "cliradar 0.2.0"


def test_returns_usage_code_for_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(["compare", "--config", str(tmp_path / "missing.yml")])

    assert code == ExitCode.USAGE
    assert "configuration error:" in capsys.readouterr().err


def test_requires_an_explicit_scan_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code = run([])

    assert code == ExitCode.USAGE
    assert "choose mode: compare, audit, or docs" in capsys.readouterr().err


class FakeSession(FakeDevice):
    """A SwitchSession stand-in: the device emulator behind the real signature."""

    def __init__(self, config: dict[str, object], raw_log: object = None) -> None:
        super().__init__()
        self.config = config
        self.raw_log = raw_log

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True

    def open_extra_sessions(self, count: int) -> list["FakeSession"]:
        return []

    def open_extra_channels(self, count: int) -> list[object]:
        return []


def test_audit_with_enter_modes_walks_the_context_graph_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The full path a release build takes: run() loads the config, the
    # navigator proves each context, the crawler maps it, and every context is
    # folded into one catalog on disk as it completes.
    monkeypatch.setattr("cliradar.session.SwitchSession", FakeSession)
    destination = tmp_path / "cli_real.yml"
    config = tmp_path / "config.yml"
    config.write_text(
        f"""
device:
  host: switch.lab
  username: auditor
output:
  documentation_catalog: {(tmp_path / "cli_doc.yml").as_posix()}
  device_catalog: {destination.as_posix()}
  comparison_catalog: {(tmp_path / "cli_compare.yml").as_posix()}
  html_report: {(tmp_path / "report.html").as_posix()}
  raw_log: {(tmp_path / "session.log").as_posix()}
  tree_catalog: {(tmp_path / "commands_tree.yml").as_posix()}
  human_catalog: {(tmp_path / "commands_human.yml").as_posix()}
""",
        encoding="utf-8",
    )

    code = run(["audit", "--config", str(config), "--enter-modes", "--quiet"])

    assert code == ExitCode.OK
    content = yaml.safe_load(destination.read_text(encoding="utf-8"))
    commands = {item["command"] for item in content["commands"]}
    assert "configure" in commands
    assert "configure vlan 1 name" in commands  # found inside a nested context
    assert content["scan"]["source"] == "context-graph"
    assert content["scan"]["executed_commands"]  # the audit trail is published
    names = {item["name"] for item in content["scan"]["contexts"]}
    assert "root" in names and "root/configure" in names


def test_docs_mode_parses_documentation_without_device_configuration(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "vendor_docs"
    docs.mkdir()
    (docs / "commands.txt").write_text("show version\n", encoding="utf-8")
    destination = tmp_path / "cli_doc.yml"
    config = tmp_path / "config.yml"
    config.write_text(
        f"""
output:
  documentation_catalog: {destination.as_posix()}
  device_catalog: {(tmp_path / "cli_real.yml").as_posix()}
  comparison_catalog: {(tmp_path / "cli_compare.yml").as_posix()}
  html_report: {(tmp_path / "missing.html").as_posix()}
""",
        encoding="utf-8",
    )

    code = run(
        [
            "docs",
            "--config",
            str(config),
            "--docs",
            str(docs),
            "--quiet",
        ]
    )

    content = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert code == ExitCode.OK
    assert content["mode"] == "docs"
    assert "device" not in content
    assert content["summary"] == {
        "documentation_commands": 1,
        "parsed": 1,
    }
    assert content["commands"][0]["documentation_status"] == "parsed"
    assert not (tmp_path / "missing.html").exists()


def test_capture_firmware_records_every_configured_command() -> None:
    from cliradar.cli import capture_firmware

    class FakeSession:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run_command(self, command: str) -> str:
            self.commands.append(command)
            return f"  output of {command}  "

    session = FakeSession()
    firmware = capture_firmware(session, ("show version", "show system"))

    assert session.commands == ["show version", "show system"]
    assert firmware["results"] == [
        {"command": "show version", "output": "output of show version"},
        {"command": "show system", "output": "output of show system"},
    ]


def test_capture_firmware_survives_a_device_that_rejects_the_command() -> None:
    from cliradar.cli import capture_firmware

    class AngrySession:
        def run_command(self, command: str) -> str:
            raise TimeoutError("no response")

    firmware = capture_firmware(AngrySession(), ("show version",))

    assert firmware["results"] == [{"command": "show version", "error": "no response"}]


def test_package_is_runnable_with_python_dash_m() -> None:
    """The stand runbooks invoke `python -m cliradar` on an uninstalled checkout."""
    import importlib.util

    from cliradar.cli import main

    spec = importlib.util.find_spec("cliradar.__main__")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.main is main


def test_firmware_stamp_keeps_the_version_and_drops_device_identity() -> None:
    from cliradar.cli import capture_firmware

    banner = (
        "show version\r\n"
        "SwitchOS Software, Version 8.4.2\r\n"
        "Build 771, compiled 2026-03-14\r\n"
        "Hostname: SW-CORE-01\r\n"
        "Serial Number: FOC1234X5YZ\r\n"
        "Base ethernet MAC Address: 00:1B:0D:AA:BB:CC\r\n"
        "Uptime: 41 days\r\n"
        "SW-CORE-01#"
    )

    class FakeSession:
        def run_command(self, command: str) -> str:
            return banner

    output = capture_firmware(FakeSession(), ("show version",))["results"][0]["output"]

    assert "Version 8.4.2" in output
    assert "Build 771" in output
    assert "Uptime: 41 days" in output
    for identifier in ("SW-CORE-01", "FOC1234X5YZ", "00:1B:0D:AA:BB:CC"):
        assert identifier not in output


def test_firmware_stamp_drops_credentials_from_a_configuration_dump() -> None:
    from cliradar.cli import capture_firmware

    dump = (
        "show running-config\r\n"
        "version 8.4.2\r\n"
        "enable secret 5 $1$abc$Xyz123\r\n"
        "username admin password 7 070C285F4D06\r\n"
        "snmp-server community s3cr3t RO\r\n"
        "interface eth0\r\n"
        "SW-CORE-01#"
    )

    class FakeSession:
        def run_command(self, command: str) -> str:
            return dump

    output = capture_firmware(FakeSession(), ("show running-config",))["results"][0]["output"]

    assert "version 8.4.2" in output
    assert "interface eth0" in output
    for secret in ("$1$abc$Xyz123", "070C285F4D06", "s3cr3t"):
        assert secret not in output
