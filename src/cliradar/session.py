from __future__ import annotations

import os
import re
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

import paramiko

from .parser import clean_terminal_output

PAGER_RE = re.compile(
    r"(?:--+\s*more\s*--+|----\s*more\b.*?----|press\s+(?:any\s+key|space)|"
    r"\(\s*q\s*\)\s*uit)",
    re.IGNORECASE,
)
# A device that asks for confirmation keeps reading the line: everything typed
# next would answer the dialog instead of running as a command. The forms vary
# more than one pattern first suggests - VRP writes "[Y/N]", "(y/n)", "(y or
# n)", "[yes,no] (no)" and a bare "Continue?", and an unmatched dialog swallows
# every later keystroke, so the net is drawn wide on purpose.
CONFIRM_RE = re.compile(
    r"\(\s*y\s*/\s*n\s*\)|\[\s*y\s*/\s*n\s*\]|\(\s*yes\s*/\s*no\s*\)|"
    r"\[\s*yes\s*/\s*no\s*\]|\(\s*y\s+or\s+n\s*\)|\[\s*yes\s*,\s*no\s*\]|"
    r"\[confirm\]|\bconfirm\b|continue\s*\?|proceed\s*\?|are\s+you\s+sure|"
    r"press\s+.*to\s+(?:confirm|continue)",
    re.IGNORECASE,
)

# After 'enable' a Cisco-like CLI either elevates straight to a '#' prompt or
# asks for a secret first. A wrong secret is rejected with one of these; the
# net is drawn a little wide because vendors word the refusal differently.
ENABLE_PASSWORD_RE = re.compile(r"(?i)pass\s*word\s*:\s*$")
ENABLE_FAIL_RE = re.compile(
    r"(?i)bad\s+(?:password|secret)s?|access\s+denied|permission\s+denied|"
    r"authentication\s+fail|%\s*error|%\s*bad|invalid\s+password|"
    r"password\s+required"
)

_LOG_LOCK = threading.Lock()

# Failures worth another attempt: the network refused, timed out or hung up.
# Authentication and host-key refusals are deliberately absent - retrying them
# only repeats a rejection, and a wrong password must surface at once.
_RETRYABLE = (paramiko.SSHException, OSError, TimeoutError, EOFError)
_FATAL = (paramiko.AuthenticationException, paramiko.BadHostKeyException)

# An upper bound on the pause between connection attempts. The backoff doubles
# from device.retry_backoff; without a cap a long-retry configuration would
# leave the scan asleep for minutes.
MAX_BACKOFF_SECONDS = 8.0


class SessionDropped(ConnectionError):
    """The transport died under a command that had already been sent.

    Deliberately an OSError: every caller in the crawl already treats an OSError
    from the terminal as "the position is lost, recover" - the navigator reopens
    the channel and replays the entry path. Paramiko's own SSHException is not
    an OSError, so without this translation a mid-scan disconnect escaped all
    the way out and ended a scan that was perfectly recoverable.
    """


class SwitchSession:
    def __init__(self, config: dict[str, Any], raw_log: Path | None = None) -> None:
        self.config = config
        self.raw_log = raw_log
        self.client: paramiko.SSHClient | None = None
        self.channel: paramiko.Channel | None = None
        self.max_response_bytes = int(config.get("max_response_bytes", 2 * 1024 * 1024))
        self.log_failures = 0
        # How often the transport had to be rebuilt, and how often an attempt
        # was retried after a transient failure. Both are reported by the scan,
        # so a flaky link shows up as a number instead of as a mystery.
        self.reconnects = 0
        self.connect_retries_used = 0
        self._siblings: list[SwitchSession] = []

    def __enter__(self) -> Self:
        self._connect()
        return self

    def _connect(self) -> None:
        """Open the session, retrying a transient failure with a backoff.

        A scan that starts at the wrong moment - a device still booting an
        interface, a firewall state table mid-flush - used to end on the first
        refusal. Attempts are spaced by a doubling pause; a rejected password or
        host key is raised at once, because no amount of waiting fixes it.
        """
        attempts = max(0, int(self.config.get("connect_retries", 2))) + 1
        pause = max(0.0, float(self.config.get("retry_backoff", 1.0)))
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                self._connect_once()
                return
            except _FATAL:
                raise
            except _RETRYABLE as error:
                last = error
                if attempt == attempts - 1:
                    break
                self.connect_retries_used += 1
                self._log(f"\n### RETRY CONNECT ({attempt + 1}/{attempts - 1}): {error}\n")
                if pause:
                    time.sleep(min(pause * (2 ** attempt), MAX_BACKOFF_SECONDS))
        assert last is not None  # the loop only breaks after a failed attempt
        raise last

    def _connect_once(self) -> None:
        password_env = self.config.get("password_env", "SWITCH_PASSWORD")
        password = os.environ.get(password_env)
        if not password:
            raise RuntimeError(f"Environment variable {password_env} is not set")

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        known_hosts = self.config.get("known_hosts")
        if known_hosts and not Path(known_hosts).is_file():
            # A stale path (a moved or cleaned-up file) would otherwise surface
            # as a bare ENOENT with no hint at which setting caused it.
            raise RuntimeError(
                f"device.known_hosts points to a missing file: {known_hosts} - "
                "fix the path, or remove the setting and pin the key again"
            )
        self.client.load_host_keys(known_hosts) if known_hosts else self.client.load_system_host_keys()
        try:
            self.client.connect(
                hostname=self.config["host"],
                port=int(self.config.get("port", 22)),
                username=self.config["username"],
                password=password,
                timeout=float(self.config.get("connect_timeout", 10)),
                look_for_keys=False,
                allow_agent=False,
                disabled_algorithms={
                    "keys": ["ssh-rsa"],
                    "pubkeys": ["ssh-rsa"],
                },
            )
            self._arm_keepalive()
            self.channel = self.client.invoke_shell(width=240, height=1000)
            self._read_until_prompt()
            self._enter_privileged()
        except Exception:
            self.client.close()
            raise

    def _arm_keepalive(self) -> None:
        """Ask paramiko to send keepalives so an idle session is not dropped.

        A help query can sit for a minute behind a slow context, and devices (or
        the firewalls in front of them) hang up on a transport that says nothing
        for that long. Best-effort: a transport that refuses the setting still
        works, it is just as fragile as it was before.
        """
        interval = float(self.config.get("keepalive", 15))
        if interval <= 0 or self.client is None:
            return
        transport = self.client.get_transport()
        if transport is None:
            return
        try:
            transport.set_keepalive(int(interval))
        except (paramiko.SSHException, OSError):
            pass

    def _guard(self, action: Callable[[], str]) -> str:
        """Run a channel operation, reporting a dead transport as SessionDropped.

        Paramiko raises SSHException when the connection dies under a command.
        That is not an OSError, so it used to escape every recovery path in the
        crawl and end the scan; translated here, it lands in the same handler as
        a closed channel and the navigator rebuilds the session and carries on.
        """
        try:
            return action()
        except paramiko.SSHException as error:
            raise SessionDropped(f"the device session dropped: {error}") from error

    def __exit__(self, *_: object) -> None:
        for sibling in self._siblings:
            if sibling.channel:
                sibling.channel.close()
        self._siblings.clear()
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()

    def open_extra_sessions(self, count: int) -> list[SwitchSession]:
        """Open extra shell channels on the same SSH transport.

        Stops early if the device refuses another channel; the scan then
        simply runs with fewer workers.
        """
        sessions: list[SwitchSession] = []
        for _ in range(max(0, count)):
            if not self.client:
                break
            sibling = SwitchSession(self.config, self.raw_log)
            sibling.client = self.client
            try:
                sibling.channel = self.client.invoke_shell(width=240, height=1000)
                sibling._read_until_prompt()
                sibling._enter_privileged()
            except (paramiko.SSHException, OSError, TimeoutError, RuntimeError):
                if sibling.channel:
                    sibling.channel.close()
                break
            self._siblings.append(sibling)
            sessions.append(sibling)
        return sessions

    def open_extra_channels(self, count: int) -> list[Callable[[str], str]]:
        return [sibling.query_help for sibling in self.open_extra_sessions(count)]

    def query_help(self, prefix: str) -> str:
        return self._guard(lambda: self._query_help(prefix))

    def _query_help(self, prefix: str) -> str:
        if not self.channel:
            raise SessionDropped("the SSH session is not connected")
        self._validate_prefix(prefix)
        self.channel.send("\x15")
        self._read_available()
        self.channel.send(prefix + "?")
        output = self._read_until_help_end(prefix)
        self.channel.send("\x15")
        self._read_available()
        self._log(f"\n### QUERY {prefix}?\n{output}")
        return output

    def run_command(self, command: str) -> str:
        return self._guard(lambda: self._run_command(command))

    def _run_command(self, command: str) -> str:
        """Execute a command and return its output.

        This is the only place where the scanner submits a discovered command
        with Enter; `probe_prompt` and `interrupt` send a bare Enter to find
        out where the session stands, which executes nothing. A confirmation
        dialog is always declined: an unanswered dialog would swallow every
        later keystroke, and answering it would apply a change nobody asked for.
        """
        if not self.channel:
            raise SessionDropped("the SSH session is not connected")
        self._validate_prefix(command)
        self.channel.send("\x15")
        self._read_available()
        self.channel.send(command + "\r")
        # A finished command leaves the prompt behind, the same marker the
        # help queries use; a command that asks something instead does not,
        # and the idle timeout still catches that.
        output = self._read_until_help_end("")
        if CONFIRM_RE.search(output):
            self.channel.send("n\r")
            output += self._read_until_idle()
        self._log(f"\n### RUN {command}\n{output}")
        return output

    def capture_output(self, command: str) -> str:
        return self._guard(lambda: self._capture_output(command))

    def _capture_output(self, command: str) -> str:
        """Run a read-only command and collect everything it prints.

        `run_command` stops at the first redrawn prompt, which is right for a
        one-line answer and wrong for a configuration dump: a dump is large
        enough to arrive in many chunks, it pauses while the device formats the
        next page, and it contains lines that end in '#' - the very character
        the redraw marker looks for. So the end of a capture is the configured
        prompt pattern standing alone on the last line with nothing more
        arriving, and a device that never gets there yields what it did send
        instead of an exception; a truncated configuration still names commands.
        """
        if not self.channel:
            raise SessionDropped("the SSH session is not connected")
        self._validate_prefix(command)
        self.channel.send("\x15")
        self._read_available()
        self.channel.send(command + "\r")
        pattern = re.compile(self.config.get("prompt_pattern", r"(?m)^[^\r\n]+[>#]\s*$"))
        deadline = time.monotonic() + float(self.config.get("capture_timeout", 120))
        settle = max(float(self.config.get("idle_timeout", 0.35)), 0.5)
        last_data = time.monotonic()
        output = ""
        handled_pagers = 0
        while time.monotonic() < deadline:
            chunk = self._read_available()
            if chunk:
                output += chunk
                self._check_response_size(output)
                last_data = time.monotonic()
                pager_count = len(PAGER_RE.findall(output))
                while self.channel and handled_pagers < pager_count:
                    self.channel.send(" ")
                    handled_pagers += 1
                continue
            if output and time.monotonic() - last_data >= settle:
                tail = clean_terminal_output(output).rstrip()
                last_line = tail.rsplit("\n", 1)[-1] if tail else ""
                if pattern.search(last_line):
                    break
                # Nothing is arriving and the prompt is not back: the device is
                # either still thinking or waiting for something this command
                # was not supposed to ask. Give it the read timeout, then stop.
                if time.monotonic() - last_data >= float(self.config.get("read_timeout", 4)) * 3:
                    break
            time.sleep(0.02)
        self._log(f"\n### CAPTURE {command}\n{output}")
        return output

    def probe_prompt(self) -> str:
        return self._guard(lambda: self._probe_prompt())

    def _probe_prompt(self) -> str:
        """Ask the device to redraw its prompt without changing anything.

        An empty line is the only position check that is safe in every
        context. Ctrl-C is not: some platforms treat it as "leave the current
        configuration mode", so using it to find out where the session stands
        would move the session while measuring it.
        """
        if not self.channel:
            raise SessionDropped("the SSH session is not connected")
        # Anything still buffered belongs to the previous command; reading it
        # as the answer to this one would report a stale position.
        self._read_available()
        self.channel.send("\r")
        # The answer to an empty line is the prompt itself, so the same
        # end-of-output marker applies: waiting out an idle timeout here would
        # dominate the cost of probing, which checks the position twice per
        # command.
        output = self._read_until_help_end("")
        self._log(f"\n### PROMPT\n{output}")
        return output

    def interrupt(self) -> str:
        return self._guard(lambda: self._interrupt())

    def _interrupt(self) -> str:
        """Abort a pending dialog, pager or hung command.

        Recovery only: this may drop the session out of its current context,
        so it is used when nothing answers, never to check position.
        """
        if not self.channel:
            raise SessionDropped("the SSH session is not connected")
        self.channel.send("\x03")
        output = self._read_until_idle()
        self.channel.send("\r")
        output += self._read_until_idle()
        self._log(f"\n### INTERRUPT\n{output}")
        return output

    def reopen(self) -> None:
        """Get back to a known state with a fresh shell.

        A new channel on the existing transport is enough for a lost context.
        When the transport itself is gone - an idle timeout on the device, a
        dropped session - the whole connection is rebuilt, because a scan must
        not end just because the device hung up on it.
        """
        if self.channel:
            try:
                self.channel.close()
            except OSError:
                pass
        # Ask the transport whether it is alive rather than finding out from
        # the exception a dead one happens to raise.
        transport = self.client.get_transport() if self.client else None
        if transport is not None and transport.is_active():
            try:
                self.channel = self.client.invoke_shell(width=240, height=1000)
                self._read_until_prompt()
                self._enter_privileged()
                self._log("\n### REOPEN CHANNEL\n")
                return
            except (paramiko.SSHException, OSError, TimeoutError):
                pass
        if self.client:
            self.client.close()
        # A rebuild in the middle of a scan is the case the backoff in _connect
        # exists for: the device that just hung up is often a second away from
        # accepting again, and a single immediate attempt would miss it.
        self._connect()
        self.reconnects += 1
        self._log("\n### RECONNECTED\n")

    def _enter_privileged(self) -> None:
        """Raise the session to privileged ('enable') mode when configured.

        A Cisco-like login lands in an unprivileged view whose prompt ends in
        '>'; the full command surface and the running configuration sit behind
        an 'enable' step that ends in '#'. This runs it once on every freshly
        opened channel - the initial login, a reopened channel and each extra
        worker - so the whole crawl sees the same privilege level. It is a
        no-op unless device.enable is set, so a shell that already drops into
        '#' is left untouched.
        """
        if not self.config.get("enable"):
            return
        if not self.channel:
            raise RuntimeError("session is not connected")
        command = str(self.config.get("enable_command", "enable"))
        self._validate_prefix(command)
        # Clear whatever the login left buffered so the reply read below is the
        # answer to 'enable' and not a stale prompt line.
        self._read_available()
        self.channel.send(command + "\r")
        transcript, asked_password = self._read_until_match(ENABLE_PASSWORD_RE)
        if asked_password:
            env_name = str(self.config.get("enable_password_env", "ENABLE_SECRET"))
            secret = os.environ.get(env_name, "")
            self.channel.send(secret + "\r")
            transcript += self._read_until_idle()
        self._confirm_privileged(transcript)
        self._log(f"\n### ENABLE\n{transcript}")

    def _read_until_match(self, pattern: re.Pattern[str]) -> tuple[str, bool]:
        """Read until the cleaned tail matches `pattern`, or the reads go idle.

        Returns the raw output collected and whether the pattern was seen. It
        lets the enable step notice an interactive password request without
        waiting out the full read timeout on a device that never asks for one.
        """
        deadline = time.monotonic() + float(self.config.get("read_timeout", 4))
        idle_timeout = float(self.config.get("idle_timeout", 0.35))
        last_data = time.monotonic()
        output = ""
        while time.monotonic() < deadline:
            chunk = self._read_available()
            if chunk:
                output += chunk
                self._check_response_size(output)
                last_data = time.monotonic()
                if pattern.search(clean_terminal_output(output).rstrip()):
                    return output, True
            elif output and time.monotonic() - last_data >= idle_timeout:
                break
            else:
                time.sleep(0.02)
        return output, False

    def _confirm_privileged(self, transcript: str) -> None:
        """Verify the session actually reached a privileged ('#') prompt."""
        prompt = clean_terminal_output(self.probe_prompt())
        combined = clean_terminal_output(transcript) + "\n" + prompt
        if ENABLE_FAIL_RE.search(combined):
            raise RuntimeError(
                "enable was rejected; check the secret in the environment "
                f"variable device.enable_password_env "
                f"({self.config.get('enable_password_env', 'ENABLE_SECRET')})"
            )
        last_line = prompt.strip().rsplit("\n", 1)[-1].rstrip() if prompt.strip() else ""
        if not last_line.endswith("#"):
            raise RuntimeError(
                "enable did not reach a privileged prompt ('#'); the account "
                "may lack the privilege level, or device.enable_command is "
                f"wrong. The prompt was: {last_line!r}"
            )

    @staticmethod
    def _validate_prefix(prefix: str) -> None:
        if len(prefix.encode("utf-8")) > 512:
            raise ValueError("CLI prefix is longer than 512 bytes")
        if prefix and ("?" in prefix or not prefix.isascii() or not prefix.isprintable()):
            raise ValueError("CLI prefix contains unsupported or control characters")

    def _read_until_prompt(self) -> str:
        pattern = re.compile(self.config.get("prompt_pattern", r"(?m)^[^\r\n]+[>#]\s*$"))
        timeout = float(self.config.get("read_timeout", 4))
        deadline = time.monotonic() + timeout
        # Some devices open the shell and then wait: the first prompt is only
        # drawn in reply to a keystroke. An empty line is the safe nudge - it
        # redraws the prompt and executes nothing.
        nudge_times = [time.monotonic() + timeout / 4, time.monotonic() + timeout / 2]
        output = ""
        handled_pagers = 0
        while time.monotonic() < deadline:
            output += self._read_available()
            self._check_response_size(output)
            # A device that accepts the password and then closes the shell
            # channel looks exactly like a silent one: reads return nothing.
            # Only telling the two apart makes the failure diagnosable.
            if self.channel and getattr(self.channel, "closed", False):
                self._raise_login_channel_closed(output)
            if self.channel and nudge_times and time.monotonic() >= nudge_times[0]:
                nudge_times.pop(0)
                try:
                    self.channel.send("\r")
                except OSError:
                    self._raise_login_channel_closed(output)
            # A login banner can be paged, and the prompt only appears after
            # the last page is acknowledged.
            pager_count = len(PAGER_RE.findall(output))
            while self.channel and handled_pagers < pager_count:
                self.channel.send(" ")
                handled_pagers += 1
            # The pattern is matched against what a person would see: colour
            # codes and cursor movement around the prompt must not hide it.
            if pattern.search(clean_terminal_output(output)):
                self._log(f"\n### LOGIN\n{output}")
                return output
            time.sleep(0.05)
        # This runs before anything is logged, so the error itself must show
        # what the device printed instead of a prompt.
        tail = clean_terminal_output(output).strip()[-300:] or "<nothing>"
        self._log(f"\n### LOGIN TIMEOUT\n{output}")
        raise TimeoutError(
            "Device prompt was not detected; adjust device.prompt_pattern "
            f"or device.read_timeout. The device sent: {tail!r}"
        )

    def _raise_login_channel_closed(self, output: str) -> None:
        tail = clean_terminal_output(output).strip()[-300:]
        self._log(f"\n### LOGIN CHANNEL CLOSED\n{output}")
        raise RuntimeError(
            "the device closed the shell channel right after login; the "
            "account may have no interactive CLI access, or the CLI lives on "
            "another port. "
            + (f"The device sent: {tail!r}" if tail else "The device sent nothing.")
        )

    def _read_until_help_end(self, prefix: str) -> str:
        """Read a help response, stopping as soon as the device redraws the line.

        After answering `?` a Cisco-like CLI reprints "prompt + the text typed
        so far". That redraw is a deterministic end-of-output marker, so the
        response can be collected in one round trip instead of waiting out an
        idle timeout on every query - the difference between hours and minutes
        on a full command tree. Platforms that do not redraw simply fall back
        to the idle timeout.
        """
        # The redraw is matched at the end of the response rather than at the
        # start of a line: a dismissed pager leaves erase sequences on the same
        # line, so the prompt is rarely the first thing on it.
        typed = prefix.strip()
        end_marker = re.compile(
            r"\S[>#\$][ \t]*" + re.escape(typed) + r"[ \t]*$" if typed
            else r"\S[>#\$][ \t]*$"
        )
        deadline = time.monotonic() + float(self.config.get("read_timeout", 4))
        idle_timeout = float(self.config.get("idle_timeout", 0.35))
        last_data = time.monotonic()
        output = ""
        handled_pagers = 0
        while time.monotonic() < deadline:
            chunk = self._read_available()
            if chunk:
                output += chunk
                self._check_response_size(output)
                last_data = time.monotonic()
                pager_count = len(PAGER_RE.findall(output))
                while self.channel and handled_pagers < pager_count:
                    self.channel.send(" ")
                    handled_pagers += 1
                    continue
                if handled_pagers == pager_count:
                    # A dismissed pager is erased with backspaces and the
                    # prompt is redrawn on the same line, so the tail has to
                    # be cleaned before the marker can be recognised.
                    tail = clean_terminal_output(output[-512:]).rstrip("\n")
                    if end_marker.search(tail):
                        return output
            elif output and time.monotonic() - last_data >= idle_timeout:
                return output
            else:
                time.sleep(0.01)
        return output

    def _read_until_idle(self) -> str:
        deadline = time.monotonic() + float(self.config.get("read_timeout", 4))
        idle_timeout = float(self.config.get("idle_timeout", 0.35))
        last_data = time.monotonic()
        output = ""
        handled_pagers = 0
        while time.monotonic() < deadline:
            chunk = self._read_available()
            if chunk:
                output += chunk
                self._check_response_size(output)
                last_data = time.monotonic()
                pager_count = len(PAGER_RE.findall(output))
                while self.channel and handled_pagers < pager_count:
                    self.channel.send(" ")
                    handled_pagers += 1
            elif output and time.monotonic() - last_data >= idle_timeout:
                return output
            time.sleep(0.03)
        return output

    def _read_available(self) -> str:
        if not self.channel:
            return ""
        chunks: list[bytes] = []
        received = 0
        while self.channel.recv_ready():
            chunk = self.channel.recv(min(65535, self.max_response_bytes - received + 1))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > self.max_response_bytes:
                raise RuntimeError("Device response exceeded device.max_response_bytes")
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _check_response_size(self, output: str) -> None:
        if len(output.encode("utf-8")) > self.max_response_bytes:
            raise RuntimeError("Device response exceeded device.max_response_bytes")

    def _log(self, text: str) -> None:
        """Append to the session log; never let logging end a scan.

        The log is a troubleshooting aid, not the result. A locked or full
        disk must not destroy a crawl that has been running for an hour, so
        write failures are counted and reported instead of raised. Refusing an
        unsafe destination stays fatal - that is a decision, not a failure.
        """
        if not self.raw_log:
            return
        with _LOG_LOCK:
            try:
                self._log_locked(text)
            except OSError:
                self.log_failures += 1

    def _log_locked(self, text: str) -> None:
        if self.raw_log:
            self.raw_log.parent.mkdir(parents=True, exist_ok=True)
            if self.raw_log.is_symlink():
                raise RuntimeError("Refusing to write a session log through a symbolic link")
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.raw_log, flags, 0o600)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                else:
                    os.chmod(self.raw_log, stat.S_IREAD | stat.S_IWRITE)
            except Exception:
                os.close(fd)
                raise
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(text)
