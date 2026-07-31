from pathlib import Path

import pytest

from cliradar.config import load_config
from cliradar.exceptions import ConfigurationError


def test_loads_and_validates_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
discovery:
  max_depth: 4
  max_queries: 100
  parameter_samples:
    WORD: Ethernet1/1
output:
  documentation_catalog: output/docs.yml
  device_catalog: output/device.yml
  comparison_catalog: output/compare.yml
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.device.host == "switch.example.test"
    assert config.discovery.max_depth == 4
    assert config.discovery.parameter_samples == (("WORD", "Ethernet1/1"),)
    assert config.output.documentation_catalog == Path("output/docs.yml")
    assert config.output.device_catalog == Path("output/device.yml")
    assert config.output.comparison_catalog == Path("output/compare.yml")
    assert config.output.html_report == Path("output/missing_commands.html")


def test_docs_configuration_does_not_require_device_credentials(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("output: {}", encoding="utf-8")

    config = load_config(path, require_device=False)

    assert config.output.documentation_catalog == Path("output/cli_doc.yml")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("device: []", "device must be a YAML mapping"),
        ("device:\n  host: device.example.invalid\n  username: user", "device.host"),
        (
            "device:\n  host: switch\n  username: user\n  port: 70000",
            "device.port",
        ),
        (
            "device:\n  host: switch\n  username: user\ndiscovery:\n  parameter_policy: all",
            "parameter_policy",
        ),
        (
            "device:\n  host: switch\n  username: user\ndiscovery:\n  parameter_samples: []",
            "parameter_samples",
        ),
        (
            "device:\n  host: switch\n  username: user\ndiscovery:\n  seed_commands: show",
            "seed_commands",
        ),
        (
            "device:\n  host: switch\n  username: user\ndiscovery:\n  denied_tokens: reload",
            "denied_tokens",
        ),
        (
            """device:
  host: switch
  username: user
output:
  documentation_catalog: output/report.html
  device_catalog: output/device.yml
  comparison_catalog: output/compare.yml
  html_report: output/report.html
""",
            "output paths",
        ),
    ],
)
def test_rejects_invalid_configuration(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_config(path)


def test_version_commands_default_and_override(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
""",
        encoding="utf-8",
    )
    assert load_config(path).discovery.version_commands == ("show version",)

    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
discovery:
  version_commands:
    - display version
    - show running-config
""",
        encoding="utf-8",
    )
    assert load_config(path).discovery.version_commands == (
        "display version",
        "show running-config",
    )

    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
discovery:
  version_commands: []
""",
        encoding="utf-8",
    )
    assert load_config(path).discovery.version_commands == ()


@pytest.mark.parametrize(
    "command",
    ["write memory", "reload", "erase startup-config", "no logging", "copy a b"],
)
def test_version_commands_reject_a_command_that_changes_the_device(
    tmp_path: Path, command: str
) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        f"""
device:
  host: switch.example.test
  username: readonly
discovery:
  version_commands:
    - {command}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="version_commands"):
        load_config(path)


def test_telnet_transport_defaults_to_the_telnet_port(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
  transport: telnet
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.device.port == 23
    assert config.device.to_session_dict()["port"] == 23


def test_an_explicit_port_still_wins_over_the_transport_default(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        """
device:
  host: switch.example.test
  username: readonly
  transport: telnet
  port: 2323
""",
        encoding="utf-8",
    )

    assert load_config(path).device.port == 2323
