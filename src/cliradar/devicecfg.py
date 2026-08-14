"""Read, write and reach-test the device section of a CLIRadar config.

The interactive menu edits the target here rather than making the operator hand
-write YAML and export an environment variable. Two rules shape this module:

* The password is never written to disk. Only the non-secret fields - host,
  port, username, transport, and the name of the password's environment
  variable - are persisted; the secret itself is placed in the process
  environment by the menu, exactly as a shell ``export`` would.
* Everything else in the file is preserved. The config carries discovery and
  output sections the menu does not touch, so saving reloads the document,
  replaces only ``device.*``, and writes the rest back unchanged.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from pathlib import Path

import yaml

# The fields the menu edits, with the defaults a blank form starts from. The
# password is deliberately absent: it lives in the environment, never the file.
_DEVICE_DEFAULTS: dict[str, object] = {
    "host": "",
    "port": 22,
    "username": "",
    "transport": "ssh",
    "password_env": "SWITCH_PASSWORD",
}

# Conventional ports, so switching transport in the form offers a sane port.
CONVENTIONAL_PORT = {"ssh": 22, "telnet": 23}


def load_device_fields(config_path: Path) -> dict[str, object]:
    """The editable device fields from a config, filled in with defaults.

    A missing or unreadable file yields the defaults so the form still opens on
    a fresh checkout; unknown extra keys in the file are ignored here (they are
    kept on save, just not shown in the form).
    """
    fields = dict(_DEVICE_DEFAULTS)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return fields
    device = data.get("device") if isinstance(data, dict) else None
    if isinstance(device, dict):
        for key in fields:
            if device.get(key) is not None:
                fields[key] = device[key]
    return fields


def save_device_fields(config_path: Path, fields: dict[str, object]) -> None:
    """Write the edited device fields back, preserving the rest of the config.

    The document is reloaded and only its ``device`` mapping is updated, so
    discovery and output settings survive. The password is never among the
    written keys; ``password_env`` names where the secret is read from instead.
    """
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    device = data.get("device")
    if not isinstance(device, dict):
        device = {}
    for key in _DEVICE_DEFAULTS:
        if key in fields:
            device[key] = fields[key]
    device.pop("password", None)  # never persist a secret, even if one slipped in
    data["device"] = device
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def probe_reachable(
    host: str,
    port: int,
    timeout: float = 5.0,
    on_tick: Callable[[float], None] | None = None,
) -> tuple[bool, str]:
    """Non-blocking TCP reach test to host:port, ticking progress as it waits.

    This proves the transport port answers - the "is the switch there" a person
    wants before a run - without opening an authenticated session or typing a
    single command at the device. ``on_tick`` is called with a 0..1 fraction of
    the timeout elapsed, so the menu can animate a bar. Returns (reachable, note)
    where the note explains a failure in one short phrase.
    """
    if not host:
        return False, "no host set"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    start = time.monotonic()
    deadline = start + max(0.1, timeout)
    try:
        err = sock.connect_ex((host, port))
    except socket.gaierror:
        sock.close()
        return False, "cannot resolve host"
    except OSError as error:
        sock.close()
        return False, str(error)
    try:
        import select

        while True:
            now = time.monotonic()
            if on_tick is not None:
                on_tick(min(1.0, (now - start) / max(0.1, timeout)))
            if now >= deadline:
                return False, "timed out - no route or port closed"
            _, writable, _ = select.select([], [sock], [], min(0.1, deadline - now))
            if writable:
                error_code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error_code == 0:
                    if on_tick is not None:
                        on_tick(1.0)
                    return True, "reachable"
                return False, "connection refused"
    finally:
        sock.close()
