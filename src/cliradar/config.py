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
    # A configuration dump is orders of magnitude larger than a help answer and
    # arrives in pages, so it gets its own budget instead of the read timeout.
    capture_timeout: float = 120.0
    max_response_bytes: int = 2 * 1024 * 1024
    # Long scans die on idle disconnects more often than on anything else: the
    # device (or a firewall between) drops a session that says nothing for a
    # few minutes, and a help query can sit behind a slow context for exactly
    # that long. A keepalive packet every few seconds keeps the transport
    # visibly alive; 0 turns it off.
    keepalive: float = 15.0
    # A dropped connection is usually momentary, so opening one is retried a
    # couple of times with a growing pause instead of ending the scan. Auth
    # and host-key refusals are never retried - they will not become true.
    connect_retries: int = 2
    retry_backoff: float = 1.0
    known_hosts: str | None = None
    # A Cisco-like login lands in an unprivileged view whose prompt ends in
    # '>'; the full command surface and the running configuration sit behind an
    # 'enable' step that ends in '#'. Turn this on to raise the session to
    # privileged mode right after login. The secret is read from the
    # environment variable named by enable_password_env (leave it unset when
    # the device grants enable without a password).
    enable: bool = False
    enable_command: str = "enable"
    enable_password_env: str = "ENABLE_SECRET"

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
        if self.enable:
            if not self.enable_command.strip():
                raise ConfigurationError(
                    "device.enable_command must not be empty when device.enable is set"
                )
            if not self.enable_password_env or not self.enable_password_env.isidentifier():
                raise ConfigurationError(
                    "device.enable_password_env must be a valid environment variable name"
                )
        if not 0 <= self.keepalive <= 3600:
            raise ConfigurationError("device.keepalive must be between 0 and 3600")
        if not 0 <= self.connect_retries <= 10:
            raise ConfigurationError("device.connect_retries must be between 0 and 10")
        if not 0 <= self.retry_backoff <= 60:
            raise ConfigurationError("device.retry_backoff must be between 0 and 60")
        if not 0 < self.capture_timeout <= 3600:
            raise ConfigurationError(
                "device.capture_timeout must be greater than 0 and at most 3600"
            )
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
            "capture_timeout": self.capture_timeout,
            "max_response_bytes": self.max_response_bytes,
            "keepalive": self.keepalive,
            "connect_retries": self.connect_retries,
            "retry_backoff": self.retry_backoff,
            "known_hosts": self.known_hosts,
            "enable": self.enable,
            "enable_command": self.enable_command,
            "enable_password_env": self.enable_password_env,
        }


@dataclass(frozen=True)
class DiscoveryConfig:
    max_depth: int = 32
    max_queries: int = 100_000
    # A wall-clock ceiling for the whole run, in seconds. max_queries bounds each
    # context on its own, so a graph with many contexts has no total bound and a
    # full crawl can run for many minutes and keep loading a fragile device. This
    # is the one guarantee that a run always ends: when the deadline passes the
    # scan stops cleanly and reports itself incomplete. 0 means no time limit.
    max_runtime: float = 0.0
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
    # Whether a context-opening probe may type a value the operator never
    # supplied. Off by default: the minimum of a range reads as innocent and is
    # not - inside an interface view `speed <10-40000>` becomes `speed 10` on a
    # live port. Turn it on for a lab device to reach every last context.
    probe_invented_values: bool = False
    # How a context-opening probe chooses what to type. 'safe' (default) probes
    # only commands whose head verb enters a container the session can leave
    # again; every other statement at a config prompt takes effect when typed,
    # so it is reported instead of run. 'aggressive' probes every executable
    # leaf - it reaches more modes but mutates the device, so it is for a lab.
    probe_policy: str = "safe"
    # The head verbs the safe policy treats as mode entries. Empty means the
    # built-in set (navigator.DEFAULT_MODE_ENTRY_VERBS); list them here to add a
    # platform's own container verb. Ignored under the aggressive policy.
    mode_entry_verbs: tuple[str, ...] = ()
    # In 'compare' mode every documented command is re-queried on the device to
    # catch ones the blind crawl missed behind a parameter or context. A full
    # vendor manual holds tens of thousands of them, and verifying all of them
    # is thousands of round-trips that can run for many minutes (and hammer a
    # fragile control plane), so the pass is capped: the surplus is reported as
    # unverified rather than run. 0 lifts the cap and verifies every command.
    compare_verify_limit: int = 2000
    # Read-only commands run once before the crawl so every report states which
    # firmware it describes. A catalog without that stamp cannot be compared
    # against a later run: identical command sets mean nothing across versions.
    # Vendors disagree on the verb, so this is configurable and best-effort.
    version_commands: tuple[str, ...] = ("show version",)
    # Read-only commands that print the running configuration. They are tried
    # in order and the first one that answers is used, so the default covers
    # both dialects without the operator naming the platform.
    config_commands: tuple[str, ...] = (
        "display current-configuration",
        "show running-config",
    )
    # How this platform's contextual help deviates from the strict reading.
    # Both cost precision when wrong, so they stay off until a lab run shows
    # the behaviour - see `parser.ParserProfile`.
    accept_undescribed_options: bool = False
    error_words_are_commands: bool = False

    def validate(self) -> None:
        if not 1 <= self.max_depth <= 64:
            raise ConfigurationError("discovery.max_depth must be between 1 and 64")
        if not 1 <= self.max_queries <= 1_000_000:
            raise ConfigurationError("discovery.max_queries must be between 1 and 1000000")
        if not 0 <= self.max_runtime <= 86_400:
            raise ConfigurationError("discovery.max_runtime must be between 0 and 86400")
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
        if not 0 <= self.compare_verify_limit <= 1_000_000:
            raise ConfigurationError(
                "discovery.compare_verify_limit must be between 0 and 1000000"
            )
        if self.parameter_policy not in {"skip", "explore"}:
            raise ConfigurationError("discovery.parameter_policy must be 'skip' or 'explore'")
        if self.probe_policy not in {"safe", "aggressive"}:
            raise ConfigurationError("discovery.probe_policy must be 'safe' or 'aggressive'")
        for verb in self.mode_entry_verbs:
            if not verb or not verb.isascii() or not verb.isprintable() or " " in verb:
                raise ConfigurationError(
                    "discovery.mode_entry_verbs must be single-word head verbs"
                )
        for command in self.seed_commands:
            if not command or not command.isascii() or not command.isprintable() or "?" in command:
                raise ConfigurationError("discovery.seed_commands contains an unsafe command")
        for setting, commands in (
            ("version_commands", self.version_commands),
            ("config_commands", self.config_commands),
        ):
            for command in commands:
                if (
                    not command
                    or not command.isascii()
                    or not command.isprintable()
                    or "?" in command
                ):
                    raise ConfigurationError(
                        f"discovery.{setting} contains an unsafe command"
                    )
                # These commands are executed for real, so a typo here would
                # change the device instead of describing it.
                if any(token.lower() in WRITE_VERBS for token in command.split()):
                    raise ConfigurationError(
                        f"discovery.{setting} must only read state; "
                        f"{command!r} can modify the device"
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
    # The device's own configuration, parsed. It is written apart from the
    # catalog on purpose: the catalog is a description of a platform and can be
    # shared, this file describes one customer's network and cannot.
    config_tree: Path = Path("output/config_tree.yml")
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
            self.config_tree,
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
    mode_entry_verbs = _sequence(discovery, "mode_entry_verbs")
    version_commands = (
        _sequence(discovery, "version_commands")
        if "version_commands" in discovery
        else list(DiscoveryConfig.version_commands)
    )
    config_commands = (
        _sequence(discovery, "config_commands")
        if "config_commands" in discovery
        else list(DiscoveryConfig.config_commands)
    )
    parser_profile = discovery.get("parser_profile", {})
    if not isinstance(parser_profile, dict):
        raise ConfigurationError("discovery.parser_profile must be a YAML mapping")
    unknown = set(parser_profile) - {"accept_undescribed_options", "error_words_are_commands"}
    if unknown:
        raise ConfigurationError(
            f"discovery.parser_profile has unknown settings: {', '.join(sorted(unknown))}"
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
                capture_timeout=float(device.get("capture_timeout", 120)),
                max_response_bytes=int(device.get("max_response_bytes", 2 * 1024 * 1024)),
                keepalive=float(device.get("keepalive", 15)),
                connect_retries=int(device.get("connect_retries", 2)),
                retry_backoff=float(device.get("retry_backoff", 1.0)),
                known_hosts=(
                    str(device["known_hosts"]) if device.get("known_hosts") is not None else None
                ),
                enable=bool(device.get("enable", False)),
                enable_command=str(device.get("enable_command", "enable")),
                enable_password_env=str(device.get("enable_password_env", "ENABLE_SECRET")),
            ),
            discovery=DiscoveryConfig(
                max_depth=int(discovery.get("max_depth", 32)),
                max_queries=int(discovery.get("max_queries", 100_000)),
                max_runtime=float(discovery.get("max_runtime", 0)),
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
                compare_verify_limit=int(discovery.get("compare_verify_limit", 2000)),
                probe_invented_values=bool(
                    discovery.get("probe_invented_values", False)
                ),
                probe_policy=str(discovery.get("probe_policy", "safe")),
                mode_entry_verbs=tuple(str(item).lower() for item in mode_entry_verbs),
                version_commands=tuple(str(item) for item in version_commands),
                config_commands=tuple(str(item) for item in config_commands),
                accept_undescribed_options=bool(
                    parser_profile.get("accept_undescribed_options", False)
                ),
                error_words_are_commands=bool(
                    parser_profile.get("error_words_are_commands", False)
                ),
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
                config_tree=Path(output.get("config_tree", "output/config_tree.yml")),
                raw_log=Path(output["raw_log"]) if output.get("raw_log") else None,
            ),
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"configuration contains an invalid value: {error}") from error
    config.validate(require_device=require_device)
    return config
