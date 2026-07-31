from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigurationError

DEFAULT_DENIED_TOKENS: tuple[str, ...] = ()

# Verbs that change the device rather than describe it. Only consulted for
# commands CLIRadar executes on purpose, never for the help crawl.
WRITE_VERBS: frozenset[str] = frozenset(
    {
        "clear", "configure", "copy", "del", "delete", "erase", "format",
        "move", "no", "reboot", "reload", "remove", "rename", "reset",
        "restart", "rmdir", "shutdown", "write", "zero",
    }
)


@dataclass(frozen=True)
class DeviceConfig:
    host: str = ""
    port: int = 22
    username: str = ""
    password_env: str = "SWITCH_PASSWORD"
    transport: str = "ssh"
    prompt_pattern: str = r"(?m)^[^\r\n]+[>#]\s*$"
    connect_timeout: float = 10.0
    read_timeout: float = 4.0
    idle_timeout: float = 0.35
    max_response_bytes: int = 2 * 1024 * 1024
    known_hosts: str | None = None

    def validate(self) -> None:
        if not self.host or self.host == "device.example.invalid":
            raise ConfigurationError("device.host must contain the target hostname or IP")
        if not self.username:
            raise ConfigurationError("device.username is required")
        if self.transport not in {"ssh", "telnet"}:
            raise ConfigurationError("device.transport must be 'ssh' or 'telnet'")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("device.port must be between 1 and 65535")
        if not self.password_env or not self.password_env.isidentifier():
            raise ConfigurationError("device.password_env must be a valid environment variable name")
        for name, value in (
            ("connect_timeout", self.connect_timeout),
            ("read_timeout", self.read_timeout),
            ("idle_timeout", self.idle_timeout),
        ):
            if not 0 < value <= 300:
                raise ConfigurationError(f"device.{name} must be greater than 0 and at most 300")
        if not 1024 <= self.max_response_bytes <= 100 * 1024 * 1024:
            raise ConfigurationError(
                "device.max_response_bytes must be between 1024 and 104857600"
            )
        try:
            re.compile(self.prompt_pattern)
        except re.error as error:
            raise ConfigurationError(f"device.prompt_pattern is invalid: {error}") from error

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password_env": self.password_env,
            "transport": self.transport,
            "prompt_pattern": self.prompt_pattern,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "idle_timeout": self.idle_timeout,
            "max_response_bytes": self.max_response_bytes,
            "known_hosts": self.known_hosts,
        }


@dataclass(frozen=True)
class DiscoveryConfig:
    max_depth: int = 32
    max_queries: int = 100_000
    seed_commands: tuple[str, ...] = ()
    denied_tokens: tuple[str, ...] = DEFAULT_DENIED_TOKENS
    parameter_policy: str = "explore"
    parameter_samples: tuple[tuple[str, str], ...] = ()
    parallel_channels: int = 1
    # Entering configuration contexts is the only feature that executes
    # commands on the device, so it stays off unless it is asked for.
    enter_modes: bool = False
    max_contexts: int = 64
    # How many context-opening probes to run per context. A large context can
    # offer thousands of candidates; the excess is reported in `probes_skipped`
    # rather than run, so raise this when the goal is to open every last mode.
    max_probes_per_context: int = 200
    deduplicate_subtrees: bool = True
    verify_samples: int = 25
    # Read-only commands run once before the crawl so every report states which
    # firmware it describes. A catalog without that stamp cannot be compared
    # against a later run: identical command sets mean nothing across versions.
    # Vendors disagree on the verb, so this is configurable and best-effort.
    version_commands: tuple[str, ...] = ("show version",)

    def validate(self) -> None:
        if not 1 <= self.max_depth <= 64:
            raise ConfigurationError("discovery.max_depth must be between 1 and 64")
        if not 1 <= self.max_queries <= 1_000_000:
            raise ConfigurationError("discovery.max_queries must be between 1 and 1000000")
        if not 1 <= self.parallel_channels <= 16:
            raise ConfigurationError("discovery.parallel_channels must be between 1 and 16")
        if not 1 <= self.max_contexts <= 512:
            raise ConfigurationError("discovery.max_contexts must be between 1 and 512")
        if not 1 <= self.max_probes_per_context <= 100_000:
            raise ConfigurationError(
                "discovery.max_probes_per_context must be between 1 and 100000"
            )
        if not 0 <= self.verify_samples <= 1000:
            raise ConfigurationError("discovery.verify_samples must be between 0 and 1000")
        if self.parameter_policy not in {"skip", "explore"}:
            raise ConfigurationError("discovery.parameter_policy must be 'skip' or 'explore'")
        for command in self.seed_commands:
            if not command or not command.isascii() or not command.isprintable() or "?" in command:
                raise ConfigurationError("discovery.seed_commands contains an unsafe command")
        for command in self.version_commands:
            if not command or not command.isascii() or not command.isprintable() or "?" in command:
                raise ConfigurationError("discovery.version_commands contains an unsafe command")
            # These commands are executed for real, so a typo here would change
            # the device instead of describing it.
            if command.split()[0].lower() in WRITE_VERBS:
                raise ConfigurationError(
                    "discovery.version_commands must only read state; "
                    f"{command.split()[0]!r} can modify the device"
                )
        for token, sample in self.parameter_samples:
            if (
                not token
                or not token.isascii()
                or not token.isprintable()
                or "?" in token
            ):
                raise ConfigurationError(
                    "discovery.parameter_samples contains an unsafe parameter token"
                )
            if (
                not sample
                or not sample.isascii()
                or not sample.isprintable()
                or "?" in sample
            ):
                raise ConfigurationError(
                    "discovery.parameter_samples contains an unsafe sample value"
                )


@dataclass(frozen=True)
class OutputConfig:
    documentation_catalog: Path = Path("output/cli_doc.yml")
    device_catalog: Path = Path("output/cli_real.yml")
    comparison_catalog: Path = Path("output/cli_compare.yml")
    html_report: Path = Path("output/missing_commands.html")
    tree_catalog: Path = Path("output/commands_tree.yml")
    human_catalog: Path = Path("output/commands_human.yml")
    raw_log: Path | None = None

    def catalog_for(self, mode: str) -> Path:
        if mode == "docs":
            return self.documentation_catalog
        if mode == "audit":
            return self.device_catalog
        if mode == "compare":
            return self.comparison_catalog
        raise ConfigurationError(f"unsupported mode: {mode}")

    def validate(self) -> None:
        paths = [
            self.documentation_catalog,
            self.device_catalog,
            self.comparison_catalog,
            self.html_report,
            self.tree_catalog,
            self.human_catalog,
        ]
        if self.raw_log is not None:
            paths.append(self.raw_log)
        if len(paths) != len(set(paths)):
            raise ConfigurationError("output paths must be different")


@dataclass(frozen=True)
class AppConfig:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self, *, require_device: bool = True) -> None:
        if require_device:
            self.device.validate()
        self.discovery.validate()
        self.output.validate()


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a YAML mapping")
    return value


def _sequence(section: dict[str, Any], name: str) -> list[Any]:
    value = section.get(name, [])
    if not isinstance(value, list):
        raise ConfigurationError(f"discovery.{name} must be a YAML sequence")
    return value


def load_config(path: Path, *, require_device: bool = True) -> AppConfig:
    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError as error:
        raise ConfigurationError(f"configuration file not found: {path}") from error
    except OSError as error:
        raise ConfigurationError(f"cannot read configuration file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a YAML mapping")

    device = _section(raw, "device")
    discovery = _section(raw, "discovery")
    output = _section(raw, "output")
    parameter_samples = discovery.get("parameter_samples", {})
    if not isinstance(parameter_samples, dict):
        raise ConfigurationError("discovery.parameter_samples must be a YAML mapping")
    seed_commands = _sequence(discovery, "seed_commands")
    denied_tokens = _sequence(discovery, "denied_tokens")
    version_commands = (
        _sequence(discovery, "version_commands")
        if "version_commands" in discovery
        else list(DiscoveryConfig.version_commands)
    )
    # A telnet CLI does not live on the SSH port, and a config that names the
    # transport but not the port would otherwise dial 22 and wait out the
    # read timeout with no hint why.
    transport = str(device.get("transport", "ssh"))
    default_port = 23 if transport == "telnet" else 22
    try:
        config = AppConfig(
            device=DeviceConfig(
                host=str(device.get("host", "")),
                port=int(device.get("port", default_port)),
                username=str(device.get("username", "")),
                password_env=str(device.get("password_env", "SWITCH_PASSWORD")),
                transport=transport,
                prompt_pattern=str(
                    device.get("prompt_pattern", r"(?m)^[^\r\n]+[>#]\s*$")
                ),
                connect_timeout=float(device.get("connect_timeout", 10)),
                read_timeout=float(device.get("read_timeout", 4)),
                idle_timeout=float(device.get("idle_timeout", 0.35)),
                max_response_bytes=int(device.get("max_response_bytes", 2 * 1024 * 1024)),
                known_hosts=(
                    str(device["known_hosts"]) if device.get("known_hosts") is not None else None
                ),
            ),
            discovery=DiscoveryConfig(
                max_depth=int(discovery.get("max_depth", 32)),
                max_queries=int(discovery.get("max_queries", 100_000)),
                seed_commands=tuple(str(item) for item in seed_commands),
                denied_tokens=tuple(
                    str(item).lower()
                    for item in denied_tokens
                ),
                parameter_policy=str(discovery.get("parameter_policy", "explore")),
                parameter_samples=tuple(
                    (str(token), str(sample))
                    for token, sample in parameter_samples.items()
                ),
                parallel_channels=int(discovery.get("parallel_channels", 1)),
                enter_modes=bool(discovery.get("enter_modes", False)),
                max_contexts=int(discovery.get("max_contexts", 64)),
                max_probes_per_context=int(discovery.get("max_probes_per_context", 200)),
                deduplicate_subtrees=bool(discovery.get("deduplicate_subtrees", True)),
                verify_samples=int(discovery.get("verify_samples", 25)),
                version_commands=tuple(str(item) for item in version_commands),
            ),
            output=OutputConfig(
                documentation_catalog=Path(
                    output.get("documentation_catalog", "output/cli_doc.yml")
                ),
                device_catalog=Path(
                    output.get("device_catalog", "output/cli_real.yml")
                ),
                comparison_catalog=Path(
                    output.get("comparison_catalog", "output/cli_compare.yml")
                ),
                html_report=Path(
                    output.get("html_report", "output/missing_commands.html")
                ),
                tree_catalog=Path(
                    output.get("tree_catalog", "output/commands_tree.yml")
                ),
                human_catalog=Path(
                    output.get("human_catalog", "output/commands_human.yml")
                ),
                raw_log=Path(output["raw_log"]) if output.get("raw_log") else None,
            ),
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"configuration contains an invalid value: {error}") from error
    config.validate(require_device=require_device)
    return config
