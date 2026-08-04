"""Telnet transport for devices whose CLI is not reachable over SSH.

Some switch builds accept an SSH password but never grant an interactive
shell - the CLI lives on telnet (port 23) or on a terminal-server console line
(reverse-telnet, port 2000+line). The navigator and crawler only need the
`Terminal` surface, and every help-reading routine in `SwitchSession` depends
solely on a channel exposing `recv_ready`/`recv`/`send`. So this module reuses
all of that and replaces only the transport: a telnet channel that answers
option negotiation, and a session that logs in over it.
"""

from __future__ import annotations

import os
import re
import socket
import time

from .session import SwitchSession

# Telnet command bytes (RFC 854) used for the small amount of option
# negotiation a line-mode CLI needs.
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
OPT_ECHO, OPT_SGA = 1, 3

USERNAME_RE = re.compile(r"(?i)(?:user\s*name|user|login)\s*:\s*$")
PASSWORD_RE = re.compile(r"(?i)pass\s*word\s*:\s*$")
BAD_LOGIN_RE = re.compile(
    r"(?i)(?:bad\s+password|no\s+such\s+user|login\s+incorrect|"
    r"access\s+denied|authentication\s+fail|permission\s+denied)"
)


class TelnetChannel:
    """A paramiko-Channel-shaped wrapper over a telnet socket.

    It presents exactly the three methods the session's read loops use -
    `recv_ready`, `recv`, `send` - and hides telnet option negotiation so the
    bytes handed upward are the clean terminal stream. Negotiation is answered
    minimally: the device may drive the line (SGA) and do the echoing; the
    scanner advertises nothing it would then have to honour.
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.sock.setblocking(False)
        self._raw = bytearray()
        self._clean = bytearray()
        self.closed = False

    def _pump(self) -> None:
        """Drain the socket and turn raw telnet bytes into clean output."""
        while True:
            try:
                chunk = self.sock.recv(65535)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self.closed = True
                break
            if not chunk:
                self.closed = True
                break
            self._raw += chunk
        self._process()

    def _process(self) -> None:
        raw = self._raw
        index = 0
        length = len(raw)
        while index < length:
            byte = raw[index]
            if byte != IAC:
                self._clean.append(byte)
                index += 1
                continue
            if index + 1 >= length:
                break  # a command split across two reads; wait for the rest
            command = raw[index + 1]
            if command == IAC:  # an escaped 0xFF is a literal data byte
                self._clean.append(IAC)
                index += 2
            elif command in (DO, DONT, WILL, WONT):
                if index + 2 >= length:
                    break
                self._respond(command, raw[index + 2])
                index += 3
            elif command == SB:
                end = raw.find(bytes([IAC, SE]), index + 2)
                if end == -1:
                    break  # sub-negotiation not fully arrived yet
                index = end + 2
            else:
                index += 2  # a two-byte command with no option (NOP, GA, ...)
        del raw[:index]

    def _respond(self, command: int, option: int) -> None:
        if command == WILL:
            # Let the device echo and suppress go-ahead; refuse the rest.
            reply = DO if option in (OPT_ECHO, OPT_SGA) else DONT
        elif command == DO:
            # Agree only to suppress go-ahead; never promise to echo.
            reply = WILL if option == OPT_SGA else WONT
        else:  # DONT / WONT - acknowledge by declining, no state to keep
            reply = WONT if command == DONT else DONT
        try:
            self.sock.sendall(bytes([IAC, reply, option]))
        except OSError:
            self.closed = True

    def recv_ready(self) -> bool:
        self._pump()
        return bool(self._clean)

    def recv(self, size: int) -> bytes:
        if not self._clean:
            self._pump()
        taken = bytes(self._clean[:size])
        del self._clean[: len(taken)]
        return taken

    def send(self, data: str | bytes) -> int:
        payload = data.encode("ascii", "replace") if isinstance(data, str) else data
        payload = payload.replace(b"\xff", b"\xff\xff")  # escape IAC in data
        self.sock.sendall(payload)
        return len(payload)

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


class TelnetSession(SwitchSession):
    """A `SwitchSession` that speaks telnet instead of SSH.

    Only the transport differs: opening the connection, logging in and
    rebuilding a dropped link. Every help query, command, position check and
    pager rule is inherited unchanged.
    """

    def _connect(self) -> None:
        self.channel = self._open_channel()
        self._login()
        self._read_until_prompt()
        self._enter_privileged()

    def _open_channel(self) -> TelnetChannel:
        timeout = float(self.config.get("connect_timeout", 10))
        sock = socket.create_connection(
            (self.config["host"], int(self.config.get("port", 23))), timeout
        )
        return TelnetChannel(sock)

    def _login(self) -> None:
        """Answer the Username/Password prompts, if the line asks for them.

        A reverse-telnet console can already sit at a shell prompt, so a login
        prompt is expected rather than required: when none appears the session
        is assumed to be open already. A rejected password is turned into a
        clear error instead of a later, mysterious "prompt not detected".
        """
        password_env = self.config.get("password_env", "SWITCH_PASSWORD")
        password = os.environ.get(password_env)
        if not password:
            raise RuntimeError(f"Environment variable {password_env} is not set")
        username = self.config.get("username", "")

        if not self._wait_for(USERNAME_RE):
            return  # no login prompt - the line is already at a CLI
        self.channel.send(username + "\r")
        if not self._wait_for(PASSWORD_RE):
            raise RuntimeError("the device accepted a username but never asked for a password")
        self.channel.send(password + "\r")

        result = self._read_login_result()
        if BAD_LOGIN_RE.search(result):
            raise RuntimeError(
                "telnet login was rejected; check device.username and the password"
            )

    def _wait_for(self, pattern: re.Pattern[str]) -> str | None:
        """Read until a prompt matches, returning None if none does in time."""
        from .parser import clean_terminal_output

        deadline = time.monotonic() + float(self.config.get("read_timeout", 4))
        output = ""
        while time.monotonic() < deadline:
            output += self._read_available()
            self._check_response_size(output)
            if self.channel and getattr(self.channel, "closed", False) and not output:
                raise RuntimeError("the device closed the telnet connection during login")
            if pattern.search(clean_terminal_output(output).rstrip()):
                return output
            time.sleep(0.05)
        return None

    def _read_login_result(self) -> str:
        """Collect what follows the password: either an error or the prompt."""
        deadline = time.monotonic() + float(self.config.get("read_timeout", 4))
        idle = float(self.config.get("idle_timeout", 0.35))
        last = time.monotonic()
        output = ""
        while time.monotonic() < deadline:
            chunk = self._read_available()
            if chunk:
                output += chunk
                self._check_response_size(output)
                last = time.monotonic()
                if BAD_LOGIN_RE.search(output):
                    return output
            elif output and time.monotonic() - last >= idle:
                break
            else:
                time.sleep(0.02)
        return output

    def reopen(self) -> None:
        """Rebuild the telnet link and log in again from scratch."""
        if self.channel:
            try:
                self.channel.close()
            except OSError:
                pass
        self.channel = self._open_channel()
        self._login()
        self._read_until_prompt()
        self._enter_privileged()
        self._log("\n### RECONNECTED (telnet)\n")

    def open_extra_sessions(self, count: int) -> list[SwitchSession]:
        """Open extra telnet connections to share a read-only traversal.

        Telnet has no channel multiplexing, so each worker is its own
        connection and login. A device that refuses more sessions simply
        yields fewer workers.
        """
        sessions: list[SwitchSession] = []
        for _ in range(max(0, count)):
            sibling = TelnetSession(self.config, self.raw_log)
            try:
                sibling._connect()
            except (OSError, TimeoutError, RuntimeError):
                if sibling.channel:
                    sibling.channel.close()
                break
            self._siblings.append(sibling)
            sessions.append(sibling)
        return sessions
