import os
import stat
from collections import deque
from pathlib import Path

import pytest

from cliradar.session import CONFIRM_RE, SwitchSession


@pytest.mark.parametrize(
    "prompt",
    [
        "[Y/N]:",
        "(y/n)",
        "(y or n)?",
        "[yes,no] (no):",
        "Continue? [Y/N]",
        "Are you sure you want to continue",
        "Proceed? [yes/no]",
        "Warning: the device will restart. Continue?",
    ],
)
def test_confirm_re_catches_every_dialog_form(prompt: str) -> None:
    # An unanswered confirmation swallows every later keystroke, so a form the
    # pattern misses is a command run against a device that thinks it is still
    # reading a yes/no. VRP writes all of these.
    assert CONFIRM_RE.search(prompt)


def test_confirm_re_leaves_ordinary_output_alone() -> None:
    assert not CONFIRM_RE.search("interface 10GE1/0/1 is up, line protocol is up")


@pytest.mark.parametrize("prefix", ["show\nreload ", "show?", "sh\u043ew "])
def test_rejects_unsafe_cli_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        SwitchSession._validate_prefix(prefix)


def test_accepts_normal_cli_prefix() -> None:
    SwitchSession._validate_prefix("")
    SwitchSession._validate_prefix("show interfaces status ")


def test_session_log_is_private(tmp_path: Path) -> None:
    log = tmp_path / "session.log"
    session = SwitchSession({}, log)

    session._log("safe help output")

    assert log.read_text(encoding="utf-8") == "safe help output"
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


def test_log_failure_does_not_end_the_scan(tmp_path: Path) -> None:
    # A locked log file must not destroy an hour-long crawl.
    session = SwitchSession({}, tmp_path / "session.log")

    def refuse(_: str) -> None:
        raise PermissionError("locked by another process")

    session._log_locked = refuse  # type: ignore[method-assign]
    session._log("help output")

    assert session.log_failures == 1


def test_refuses_symlink_log(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "session.log"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("creating symlinks requires additional privileges on this platform")

    with pytest.raises(RuntimeError, match="symbolic link"):
        SwitchSession({}, link)._log("data")


def test_ssh_disables_legacy_rsa(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.connect_options: dict[str, object] = {}

        def set_missing_host_key_policy(self, policy: object) -> None:
            pass

        def load_system_host_keys(self) -> None:
            pass

        def connect(self, **kwargs: object) -> None:
            self.connect_options = kwargs

        def get_transport(self) -> object:
            return None  # no transport to arm a keepalive on

        def invoke_shell(self, **kwargs: object) -> object:
            return object()

    client = FakeClient()
    monkeypatch.setattr("cliradar.session.paramiko.SSHClient", lambda: client)
    monkeypatch.setenv("SWITCH_PASSWORD", "not-stored")
    session = SwitchSession({"host": "device.example.invalid", "username": "readonly"})
    monkeypatch.setattr(session, "_read_until_prompt", lambda: "")

    session.__enter__()

    assert client.connect_options["disabled_algorithms"] == {
        "keys": ["ssh-rsa"],
        "pubkeys": ["ssh-rsa"],
    }


def test_limits_single_channel_read() -> None:
    class EndlessChannel:
        def recv_ready(self) -> bool:
            return True

        def recv(self, size: int) -> bytes:
            return b"x" * size

    session = SwitchSession({"max_response_bytes": 16})
    session.channel = EndlessChannel()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="exceeded"):
        session._read_available()


def test_advances_common_help_pagers() -> None:
    class PaginatedChannel:
        def __init__(self) -> None:
            self.chunks: deque[bytes] = deque([b"  show  Display commands\n--More--"])
            self.sent: list[str] = []

        def recv_ready(self) -> bool:
            return bool(self.chunks)

        def recv(self, size: int) -> bytes:
            return self.chunks.popleft()

        def send(self, value: str) -> int:
            self.sent.append(value)
            if value == " ":
                self.chunks.append(b"\n  version  Display version\n")
            return len(value)

    channel = PaginatedChannel()
    session = SwitchSession({"read_timeout": 0.1, "idle_timeout": 0.001})
    session.channel = channel  # type: ignore[assignment]

    output = session._read_until_idle()

    assert channel.sent == [" "]
    assert "version" in output


class ScriptedChannel:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks: deque[bytes] = deque(chunks)
        self.sent: list[str] = []

    def recv_ready(self) -> bool:
        return bool(self.chunks)

    def recv(self, size: int) -> bytes:
        return self.chunks.popleft()

    def send(self, value: str) -> int:
        self.sent.append(value)
        return len(value)


def test_prompt_is_recognised_behind_colour_codes() -> None:
    # Some devices decorate the prompt with ANSI colours; the pattern must see
    # what a person sees, not the escape bytes.
    session = SwitchSession({"read_timeout": 0.5})
    session.channel = ScriptedChannel(b"Welcome\r\n\x1b[32mSW1#\x1b[0m")  # type: ignore[assignment]

    assert "SW1#" in session._read_until_prompt()


def test_prompt_appears_behind_a_paged_login_banner() -> None:
    channel = ScriptedChannel(b"Terms of use\n--More--")
    original_send = channel.send

    def send(value: str) -> int:
        if value == " ":
            channel.chunks.append(b"\nSW1#")
        return original_send(value)

    channel.send = send  # type: ignore[method-assign]
    session = SwitchSession({"read_timeout": 0.5})
    session.channel = channel  # type: ignore[assignment]

    assert "SW1#" in session._read_until_prompt()


def test_silent_device_is_nudged_with_an_empty_line() -> None:
    # Observed on the stand: the shell opens but the first prompt is only
    # drawn in reply to a keystroke, so a passive read times out on nothing.
    channel = ScriptedChannel()
    original_send = channel.send

    def send(value: str) -> int:
        if value == "\r":
            channel.chunks.append(b"\r\nSW1#")
        return original_send(value)

    channel.send = send  # type: ignore[method-assign]
    session = SwitchSession({"read_timeout": 1})
    session.channel = channel  # type: ignore[assignment]

    assert "SW1#" in session._read_until_prompt()
    assert "\r" in channel.sent


def test_closed_shell_channel_is_reported_as_such() -> None:
    # Observed on the stand: the password is accepted, the shell channel is
    # closed at once, and a passive read looks exactly like a silent device.
    channel = ScriptedChannel()
    channel.closed = True
    session = SwitchSession({"read_timeout": 1})
    session.channel = channel  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="closed the shell channel"):
        session._read_until_prompt()


def test_send_on_a_dead_channel_is_reported_not_raised_raw() -> None:
    channel = ScriptedChannel()

    def send(value: str) -> int:
        raise OSError("Socket is closed")

    channel.send = send  # type: ignore[method-assign]
    session = SwitchSession({"read_timeout": 0.5})
    session.channel = channel  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="closed the shell channel"):
        session._read_until_prompt()


def test_prompt_timeout_reports_what_the_device_sent() -> None:
    # The failure happens before anything is logged, so the error message is
    # the only place the operator can see what the device printed.
    session = SwitchSession({"read_timeout": 0.1})
    session.channel = ScriptedChannel(b"Username:")  # type: ignore[assignment]

    with pytest.raises(TimeoutError, match="Username:"):
        session._read_until_prompt()


def test_closes_client_when_prompt_detection_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        def set_missing_host_key_policy(self, policy: object) -> None:
            pass

        def load_system_host_keys(self) -> None:
            pass

        def connect(self, **kwargs: object) -> None:
            pass

        def get_transport(self) -> object:
            return None

        def invoke_shell(self, **kwargs: object) -> object:
            return object()

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr("cliradar.session.paramiko.SSHClient", lambda: client)
    monkeypatch.setenv("SWITCH_PASSWORD", "not-stored")
    session = SwitchSession({"host": "ur ip", "username": "readonly"})

    def fail_prompt() -> str:
        raise TimeoutError("prompt")

    monkeypatch.setattr(session, "_read_until_prompt", fail_prompt)

    with pytest.raises(TimeoutError):
        session.__enter__()

    assert client.closed is True


# -- privileged ('enable') mode ------------------------------------------


class ReactiveChannel:
    """A channel that answers each write with a scripted reply.

    `responder(value)` returns the bytes the device would print in reply to a
    line the session sent - enough to imitate the enable handshake, where the
    prompt and password request only appear after a keystroke.
    """

    def __init__(self, responder: object) -> None:
        self.chunks: deque[bytes] = deque()
        self.sent: list[str] = []
        self.closed = False
        self._responder = responder

    def recv_ready(self) -> bool:
        return bool(self.chunks)

    def recv(self, size: int) -> bytes:
        return self.chunks.popleft()

    def send(self, value: str) -> int:
        self.sent.append(value)
        reply = self._responder(value)  # type: ignore[operator]
        if reply:
            self.chunks.append(reply)
        return len(value)


def _enable_session(**config: object) -> SwitchSession:
    base: dict[str, object] = {"enable": True, "read_timeout": 0.5, "idle_timeout": 0.02}
    base.update(config)
    return SwitchSession(base)


def test_enable_enters_privileged_mode_with_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_SECRET", "priv-pass")

    def device(value: str) -> bytes:
        if value == "enable\r":
            return b"\r\nPassword: "
        if value == "priv-pass\r" or value == "\r":
            return b"\r\nSW1#"
        return b""

    session = _enable_session()
    session.channel = ReactiveChannel(device)  # type: ignore[assignment]

    session._enter_privileged()

    assert "enable\r" in session.channel.sent
    assert "priv-pass\r" in session.channel.sent


def test_enable_without_a_password_reaches_privileged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_SECRET", raising=False)

    def device(value: str) -> bytes:
        if value in ("enable\r", "\r"):
            return b"\r\nSW1#"
        return b""

    session = _enable_session()
    session.channel = ReactiveChannel(device)  # type: ignore[assignment]

    session._enter_privileged()

    # Only the elevation command and the prompt probe - no secret was sent.
    assert session.channel.sent == ["enable\r", "\r"]


def test_enable_reports_a_rejected_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_SECRET", "wrong")

    def device(value: str) -> bytes:
        if value == "enable\r":
            return b"\r\nPassword: "
        if value == "wrong\r":
            return b"\r\n  % Bad secrets\r\nSW1>"
        return b"\r\nSW1>"

    session = _enable_session()
    session.channel = ReactiveChannel(device)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="rejected"):
        session._enter_privileged()


def test_enable_that_stays_unprivileged_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_SECRET", raising=False)

    def device(value: str) -> bytes:
        # 'enable' silently does nothing here; the prompt never leaves '>'.
        return b"\r\nSW1>"

    session = _enable_session()
    session.channel = ReactiveChannel(device)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="privileged prompt"):
        session._enter_privileged()


def test_enable_is_skipped_when_not_configured() -> None:
    session = SwitchSession({})
    session.channel = ReactiveChannel(lambda _value: b"")  # type: ignore[assignment]

    session._enter_privileged()

    assert session.channel.sent == []


# --------------------------------------------------------------------------- #
# Staying connected: keepalive, retries with backoff, recoverable drops
# --------------------------------------------------------------------------- #

class _StubTransport:
    def __init__(self) -> None:
        self.keepalive: int | None = None
        self.active = True

    def set_keepalive(self, interval: int) -> None:
        self.keepalive = interval

    def is_active(self) -> bool:
        return self.active


class _StubChannel:
    def close(self) -> None:
        pass


class _StubClient:
    """A paramiko client stand-in whose connect() can be made to fail."""

    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.transport = _StubTransport()
        self.failures = list(failures or [])
        self.attempts = 0
        self.closed = 0

    def set_missing_host_key_policy(self, policy: object) -> None:
        pass

    def load_system_host_keys(self) -> None:
        pass

    def connect(self, **_kwargs: object) -> None:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)

    def get_transport(self) -> _StubTransport:
        return self.transport

    def invoke_shell(self, **_kwargs: object) -> object:
        return _StubChannel()

    def close(self) -> None:
        self.closed += 1


def _session_with(monkeypatch, client: _StubClient, **config: object) -> SwitchSession:
    monkeypatch.setattr("cliradar.session.paramiko.SSHClient", lambda: client)
    monkeypatch.setenv("SWITCH_PASSWORD", "not-stored")
    session = SwitchSession({"host": "d.invalid", "username": "readonly", **config})
    monkeypatch.setattr(session, "_read_until_prompt", lambda: "")
    return session


def test_keepalive_is_armed_on_the_transport(monkeypatch) -> None:
    client = _StubClient()
    session = _session_with(monkeypatch, client, keepalive=25)
    session.__enter__()
    # Without this the device (or a firewall) hangs up on a session that spends
    # a minute waiting behind one slow context.
    assert client.transport.keepalive == 25


def test_keepalive_can_be_turned_off(monkeypatch) -> None:
    client = _StubClient()
    session = _session_with(monkeypatch, client, keepalive=0)
    session.__enter__()
    assert client.transport.keepalive is None


def test_connect_retries_a_transient_failure_with_a_backoff(monkeypatch) -> None:
    import paramiko

    slept: list[float] = []
    monkeypatch.setattr("cliradar.session.time.sleep", slept.append)
    client = _StubClient([TimeoutError("no route"), paramiko.SSHException("banner")])
    session = _session_with(monkeypatch, client, connect_retries=2, retry_backoff=1.0)

    session.__enter__()

    assert client.attempts == 3  # two refusals, then the connection stands
    assert session.connect_retries_used == 2
    assert slept == [1.0, 2.0]  # the pause doubles between attempts


def test_connect_backoff_is_capped(monkeypatch) -> None:
    from cliradar.session import MAX_BACKOFF_SECONDS

    slept: list[float] = []
    monkeypatch.setattr("cliradar.session.time.sleep", slept.append)
    failures = [TimeoutError("x") for _ in range(4)]
    client = _StubClient(failures)
    session = _session_with(monkeypatch, client, connect_retries=6, retry_backoff=4.0)
    session.__enter__()
    assert max(slept) <= MAX_BACKOFF_SECONDS


def test_a_rejected_password_is_not_retried(monkeypatch) -> None:
    import paramiko

    monkeypatch.setattr("cliradar.session.time.sleep", lambda _: None)
    client = _StubClient([paramiko.AuthenticationException("no")])
    session = _session_with(monkeypatch, client, connect_retries=5)
    with pytest.raises(paramiko.AuthenticationException):
        session.__enter__()
    assert client.attempts == 1  # waiting does not make a wrong password right


def test_the_last_failure_is_raised_when_every_attempt_fails(monkeypatch) -> None:
    monkeypatch.setattr("cliradar.session.time.sleep", lambda _: None)
    client = _StubClient([TimeoutError("first"), TimeoutError("last")])
    session = _session_with(monkeypatch, client, connect_retries=1, retry_backoff=0.1)
    with pytest.raises(TimeoutError, match="last"):
        session.__enter__()
    assert client.attempts == 2


def test_a_dropped_transport_is_reported_as_a_recoverable_oserror(monkeypatch) -> None:
    import paramiko

    from cliradar.session import SessionDropped

    session = SwitchSession({"host": "d.invalid", "username": "readonly"})

    def _dropped(_prefix: str) -> str:
        raise paramiko.SSHException("Socket is closed")

    monkeypatch.setattr(session, "_query_help", _dropped)
    with pytest.raises(SessionDropped) as caught:
        session.query_help("show")
    # The navigator recovers from an OSError and ends the scan on anything
    # else, so the class of the error decides whether a blip is survivable.
    assert isinstance(caught.value, OSError)
    assert "Socket is closed" in str(caught.value)


def test_commands_on_a_closed_session_are_recoverable_too() -> None:
    from cliradar.session import SessionDropped

    session = SwitchSession({"host": "d.invalid", "username": "readonly"})
    for call in (
        lambda: session.query_help("show"),
        lambda: session.run_command("show version"),
        lambda: session.capture_output("show run"),
        lambda: session.probe_prompt(),
        lambda: session.interrupt(),
    ):
        with pytest.raises(SessionDropped):
            call()


def test_reopen_rebuilds_a_dead_transport_and_counts_it(monkeypatch) -> None:
    client = _StubClient()
    session = _session_with(monkeypatch, client)
    session.__enter__()
    client.transport.active = False  # the device hung up between two queries

    session.reopen()

    assert session.reconnects == 1
    assert client.attempts == 2  # the whole connection was built again
