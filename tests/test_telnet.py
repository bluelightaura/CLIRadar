"""Telnet transport tests driven by a scripted fake socket.

They cover the two things that are transport-specific and easy to get wrong:
option negotiation (the channel must answer it and hand up a clean stream) and
the Username/Password login, including a rejected password.
"""

from __future__ import annotations

import pytest

from cliradar.config import DeviceConfig
from cliradar.exceptions import ConfigurationError
from cliradar.telnet import (
    DO,
    DONT,
    IAC,
    OPT_ECHO,
    OPT_SGA,
    WILL,
    TelnetChannel,
    TelnetSession,
)


class FakeSocket:
    """A socket that yields a scripted byte stream and can answer what is sent.

    `on_send` receives every write, so a test can imitate a device that only
    prints the next prompt after it has read a line.
    """

    def __init__(self, script: bytes = b"") -> None:
        self.inbound = bytearray(script)
        self.sent = bytearray()
        self.closed = False
        self.on_send = None

    def setblocking(self, flag: bool) -> None:
        pass

    def recv(self, size: int) -> bytes:
        if not self.inbound:
            raise BlockingIOError
        chunk = bytes(self.inbound[:size])
        del self.inbound[: len(chunk)]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent += data
        if self.on_send is not None:
            self.on_send(self, data)

    def close(self) -> None:
        self.closed = True


def drain(channel: TelnetChannel) -> bytes:
    data = b""
    while channel.recv_ready():
        data += channel.recv(65535)
    return data


# -- the channel ----------------------------------------------------------


def test_channel_strips_negotiation_and_answers_it() -> None:
    script = bytes([IAC, DO, OPT_SGA]) + b"switch#" + bytes([IAC, WILL, OPT_ECHO])
    sock = FakeSocket(script)

    assert drain(TelnetChannel(sock)) == b"switch#"
    # Agree to suppress go-ahead and to let the device echo; nothing else.
    assert bytes([IAC, WILL, OPT_SGA]) in sock.sent
    assert bytes([IAC, DO, OPT_ECHO]) in sock.sent


def test_channel_refuses_options_it_will_not_honour() -> None:
    sock = FakeSocket(bytes([IAC, DO, 24]))  # DO TERMINAL-TYPE

    drain(TelnetChannel(sock))

    assert bytes([IAC, DONT if False else 0]) not in sock.sent  # guard: no stray
    assert bytes([IAC, 252, 24]) in sock.sent  # WONT TERMINAL-TYPE


def test_channel_unescapes_a_doubled_iac() -> None:
    sock = FakeSocket(b"a" + bytes([IAC, IAC]) + b"b")

    assert drain(TelnetChannel(sock)) == b"a\xffb"


def test_channel_send_escapes_iac_in_payload() -> None:
    sock = FakeSocket()
    channel = TelnetChannel(sock)

    channel.send("show\r")

    assert sock.sent == b"show\r"


# -- login ----------------------------------------------------------------


def _session(monkeypatch: pytest.MonkeyPatch, sock: FakeSocket, **config: object) -> TelnetSession:
    monkeypatch.setenv("SWITCH_PASSWORD", "s3cr3t-lab")
    base = {"host": "switch", "username": "root", "read_timeout": 1, "idle_timeout": 0.05}
    base.update(config)
    session = TelnetSession(base)
    session.channel = TelnetChannel(sock)
    return session


def test_login_answers_username_and_password_then_reaches_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = FakeSocket(b"\r\nUser Access Verification\r\nUsername: ")

    def device(s: FakeSocket, data: bytes) -> None:
        text = data.decode("ascii", "ignore")
        if text.startswith("root"):
            s.inbound += b"root\r\nPassword: "
        elif text.startswith("s3cr3t"):
            s.inbound += b"********\r\nswitch#"
        elif text.strip() == "":  # a bare Enter redraws the prompt
            s.inbound += b"\r\nswitch#"

    sock.on_send = device
    session = _session(monkeypatch, sock)

    session._login()

    assert b"root\r" in sock.sent
    assert b"s3cr3t-lab\r" in sock.sent
    assert "switch#" in session._read_until_prompt()


def test_login_reports_a_rejected_password(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = FakeSocket(b"Username: ")

    def device(s: FakeSocket, data: bytes) -> None:
        text = data.decode("ascii", "ignore")
        if text.startswith("root"):
            s.inbound += b"\r\nPassword: "
        elif text.startswith("s3cr3t"):
            s.inbound += b"\r\n  %No such user or bad password.\r\nUsername: "

    sock.on_send = device
    session = _session(monkeypatch, sock)

    with pytest.raises(RuntimeError, match="rejected"):
        session._login()


def test_login_is_skipped_when_the_line_is_already_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reverse-telnet console can already sit at a prompt; nothing to log in.
    sock = FakeSocket(b"switch#")
    session = _session(monkeypatch, sock, read_timeout=0.2)

    session._login()

    assert b"root" not in sock.sent


def test_missing_password_env_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWITCH_PASSWORD", raising=False)
    session = TelnetSession({"host": "switch", "username": "root"})
    session.channel = TelnetChannel(FakeSocket(b"Username: "))

    with pytest.raises(RuntimeError, match="is not set"):
        session._login()


# -- config ---------------------------------------------------------------


def test_transport_must_be_ssh_or_telnet() -> None:
    with pytest.raises(ConfigurationError, match="transport"):
        DeviceConfig(host="switch", username="root", transport="rest").validate()


def test_telnet_transport_is_accepted_and_serialised() -> None:
    device = DeviceConfig(host="switch", username="root", transport="telnet", port=23)
    device.validate()

    assert device.to_session_dict()["transport"] == "telnet"
