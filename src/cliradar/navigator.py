"""Navigation between CLI contexts (configuration modes).

A CLI is treated as a graph of contexts rather than a fixed list of modes: a
context is simply a place where contextual help answers differently, and it is
identified by the shape of the prompt. Nothing here knows what a VLAN or a VRF
is - a command that changes the prompt creates a new context, whatever it is
called on this platform.

The navigator owns every keystroke that is not a help query. It never trusts
its own memory of where the session stands: the position is re-proven from the
prompt before the crawler is allowed to ask anything.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

# Hostname is dropped (it is identical everywhere) and digits are folded, so
# "SW1(config-vlan-10)#" and "SW1(config-vlan-20)#" share one fingerprint.
PROMPT_LINE_RE = re.compile(r"^(?P<host>[\w.\-]+?)(?P<mode>\([^)]*\))?(?P<level>[#>$])\s*$")
DIGITS_RE = re.compile(r"\d+")

# Commands that would end the run itself rather than reveal anything.
DEFAULT_PROBE_DENYLIST: frozenset[str] = frozenset(
    {
        "reboot", "reload", "restart", "halt", "shutdown", "poweroff",
        "erase", "format", "delete", "clear", "write", "copy", "save",
        "restore", "upgrade", "update", "boot", "install",
        # `flush` empties a live table (`flush arp all` dropped the management
        # ARP entry and reset the scanning session on real lab hardware): it is
        # a `clear` synonym and never opens a context, so it is pure risk to type.
        "flush",
        # The same acts under other vendors' verbs. `reset` is the dangerous
        # one here: on a VRP-like CLI it is what `clear` is elsewhere, and
        # `reset saved-configuration` wipes the box. `commit`/`rollback`
        # apply or revert a candidate configuration wholesale, and `request`
        # is the Junos verb that carries reboot and zeroise.
        "reset", "commit", "rollback", "request", "startup", "schedule",
        "backup", "activate", "deactivate", "apply", "revert", "zeroize",
        "zeroise", "renew", "refresh", "kill", "terminate", "disconnect",
        "free", "fixdisk", "mkdir", "rmdir", "rename", "move", "undelete",
        "patch", "load", "execute", "test",
        "logout", "exit", "quit", "end", "disable",
        "ping", "traceroute", "tracert", "telnet", "ssh", "monitor", "debug",
        "terminal", "screen-length", "more", "language",
        # Escapes into a nested foreign shell (a vendor SDK/debug CLI or a raw
        # OS shell). Its prompts collide with the root fingerprint, so the
        # position proof cannot hold there, and mapping another CLI is out of
        # scope for a command-surface audit. Observed in a lab: one such escape
        # left every position check inside it forcing a channel rebuild.
        "diagshell", "diagnose", "produce", "shell", "bash", "sh",
        "start-shell", "system-shell", "vtysh", "python", "tclsh",
        # The management path itself. `line vty` holds the authentication and
        # timeout settings of the very session doing the scanning: probing
        # inside it stopped a switch from granting new sessions halfway
        # through a run, and every context found later became unreachable.
        "line", "username", "user", "login", "authentication", "aaa",
        "service", "management", "sshd", "telnetd", "ftpd", "tftpd",
    }
)


# Head verbs that name a container the session steps *into* and can step back
# out of - entering one is reversible, so a probe here costs nothing but the
# `exit` that leaves. Everything else at a config prompt is a statement that
# takes effect the moment it is typed (`urpf enable`, `dhcp start`,
# `icmp echo-request deny`), so the safe policy probes only these and reports
# the rest instead of executing them. Deliberately excludes ambiguous verbs
# that are a mode on some platforms and an action on others (`dhcp`, `snmp`,
# `ip`); an operator whose platform enters a mode there adds it in config.
DEFAULT_MODE_ENTRY_VERBS: frozenset[str] = frozenset(
    {
        "configure", "config", "system-view",
        "interface", "vlan", "vrf", "router", "bridge-domain",
        "policy-map", "class-map", "class", "route-map",
        "address-family", "key-chain", "template", "peer-group",
        # `line vty <n>` opens the terminal-line view; it was the one common
        # instance-entered context missing here, which kept it unscanned even
        # once harvested real values made it enterable.
        "line",
    }
)


class Terminal(Protocol):
    """The slice of a session the navigator needs."""

    def query_help(self, prefix: str) -> str: ...

    def run_command(self, command: str) -> str: ...

    def probe_prompt(self) -> str: ...

    def interrupt(self) -> str: ...

    def reopen(self) -> None: ...


def mode_fingerprint(line: str) -> str | None:
    """Return the mode part of a prompt line, or None if it is not a prompt."""
    match = PROMPT_LINE_RE.match(line.strip())
    if not match:
        return None
    mode = DIGITS_RE.sub("*", match.group("mode") or "")
    return mode + match.group("level")


def fingerprint_of(output: str) -> str | None:
    """Fingerprint of the last prompt in a device response."""
    for line in reversed(output.replace("\r", "").splitlines()):
        fingerprint = mode_fingerprint(line)
        if fingerprint:
            return fingerprint
    return None


@dataclass(frozen=True)
class ModeContext:
    """A place in the CLI where help can be asked.

    `fingerprint` proves the session is here; `entry_path` replays the way in
    from the root after any loss of state. Two contexts that share a prompt but
    are reached by different commands stay distinct - on many platforms
    "interface <ethernet>" and "interface vlan" both show (config-if)# while
    offering different commands.
    """

    name: str
    fingerprint: str
    entry_path: tuple[str, ...] = ()
    parent: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        """Identity used to decide whether a context was already scanned.

        Instance numbers are folded and a bare identifier is dropped entirely,
        so "interface 10ge1/0/1" and "interface 10ge1/0/2" are one context, and
        so are "router ospf" and "router ospf 1". A keyword still separates
        them: "interface vlan 10" has the same prompt as a physical interface
        but a different command set.
        """
        if not self.entry_path:
            return (self.fingerprint, "")
        folded = DIGITS_RE.sub("*", self.entry_path[-1])
        keywords = [word for word in folded.split() if word != "*"]
        return (self.fingerprint, " ".join(keywords))

    @property
    def depth(self) -> int:
        return len(self.entry_path)


class NavigationError(RuntimeError):
    """The session could not be proven to stand in the requested context."""


@dataclass
class ModeNavigator:
    """Keeps a terminal at a known context and reports what it proved."""

    terminal: Terminal
    root_fingerprint: str = ""
    exit_commands: tuple[str, ...] = ("exit", "quit")
    root_command: str = "end"
    max_exit_attempts: int = 4
    executed: list[str] = field(default_factory=list)
    reopens: int = 0
    # The context the session was last placed in. Identity, not fingerprint:
    # two contexts can share a prompt, so only the object we navigated to
    # proves which command set is reachable from here.
    position: ModeContext | None = None

    def bind_root(self) -> str:
        """Learn the fingerprint of the context a fresh session starts in."""
        self.root_fingerprint = self.confirm_fingerprint() or ""
        if not self.root_fingerprint:
            raise NavigationError("device prompt was not recognised")
        return self.root_fingerprint

    def confirm_fingerprint(self, attempts: int = 3) -> str | None:
        """Read the prompt twice and accept it only if it repeats.

        Banners and configuration lines can end in '#'; a fingerprint that
        survives two independent reads is a prompt and not stray output. The
        read itself must not disturb the session, so it is an empty line and
        never Ctrl-C - see `Terminal.probe_prompt`.
        """
        previous: str | None = None
        for _ in range(attempts):
            try:
                current = fingerprint_of(self.terminal.probe_prompt())
            except OSError:
                # The channel is gone; that is a lost position, not a crash.
                return None
            if current and current == previous:
                return current
            previous = current
        if previous is None:
            # Nothing answers an empty line: a dialog or a hung command is
            # holding the session. Now Ctrl-C is the right tool.
            try:
                self.terminal.interrupt()
                return fingerprint_of(self.terminal.probe_prompt())
            except OSError:
                return None
        return previous

    def run(self, command: str) -> tuple[str, str | None]:
        """Execute a command and report the fingerprint it left behind."""
        self.executed.append(command)
        try:
            output = self.terminal.run_command(command)
        except OSError:
            # Recorded before sending: a command that broke the channel may
            # still have reached the device, and the audit must say so.
            self.position = None
            return "", None
        return output, fingerprint_of(output)

    def ensure(self, context: ModeContext) -> str:
        """Place the session in `context` and return the proven fingerprint.

        Staying put is checked first: probing a context runs many commands that
        do not leave it, and re-entering from the root before each one would
        cost more than the probe itself.
        """
        if self.position is context or not context.entry_path:
            current = self.confirm_fingerprint()
            if current == context.fingerprint:
                self.position = context
                return current
        self.reset_to_root()
        for command in context.entry_path:
            self.run(command)
        proven = self.confirm_fingerprint()
        if proven != context.fingerprint:
            raise NavigationError(
                f"context {context.name!r}: expected {context.fingerprint!r}, found {proven!r}"
            )
        self.position = context
        return proven

    def leave(self, parent_fingerprint: str) -> str:
        """Return to the parent context, escalating until the prompt agrees."""
        self.position = None
        for command in self.exit_commands:
            current = self.confirm_fingerprint()
            if current == parent_fingerprint:
                return current
            if current == self.root_fingerprint:
                # Some platforms drop straight to the root; the entry path can
                # replay the way back, so this is a success, not a failure.
                return current
            self.run(command)
        current = self.confirm_fingerprint()
        if current in (parent_fingerprint, self.root_fingerprint):
            return current
        self.run(self.root_command)
        current = self.confirm_fingerprint()
        if current == self.root_fingerprint:
            return current
        return self.recover()

    def reset_to_root(self) -> str:
        """Bring the session back to the root context by any available means."""
        self.position = None
        for _ in range(self.max_exit_attempts):
            current = self.confirm_fingerprint()
            if current == self.root_fingerprint:
                return current
            self.run(self.root_command if current else self.exit_commands[0])
        if self.confirm_fingerprint() == self.root_fingerprint:
            return self.root_fingerprint
        return self.recover()

    def recover(self) -> str:
        """Last resort: a fresh channel always starts at a known context."""
        self.position = None
        try:
            self.terminal.reopen()
        except Exception as error:
            # Whatever the transport failed with, the outcome is the same: the
            # session is unusable. Reporting it as a navigation failure keeps
            # the scan alive to record what it already collected.
            raise NavigationError(f"the session could not be rebuilt: {error}") from error
        self.reopens += 1
        current = self.confirm_fingerprint()
        if current != self.root_fingerprint:
            raise NavigationError(
                f"a fresh channel reported {current!r}, expected {self.root_fingerprint!r}"
            )
        return current


def is_probe_allowed(command: str, denylist: frozenset[str] = DEFAULT_PROBE_DENYLIST) -> bool:
    """Whether a command may be executed to find out if it opens a context.

    Every word is checked, not just the first. The destructive verb is often
    not the head: `reset saved-configuration` erases the box, `request system
    reboot` restarts it, and `schedule reboot` does it later - all three pass a
    head-only test because their first word describes nothing dangerous. A
    context opener is a short phrase of nouns, so refusing a phrase that
    mentions a denied verb anywhere costs coverage that was never there, while
    a single miss costs the device.
    """
    tokens = command.split()
    if not tokens:
        return False
    # A negation undoes existing configuration whatever follows it.
    if tokens[0].lower() in {"no", "undo", "default"}:
        return False
    return not any(token.lower() in denylist for token in tokens)


def probe_order(commands: Sequence[str], hints: Sequence[str]) -> list[str]:
    """Order probe candidates so that likely context openers are tried first."""
    ranking = {hint.lower(): index for index, hint in enumerate(hints)}
    return sorted(
        commands,
        key=lambda command: (
            ranking.get(command.split()[0].lower(), len(ranking)),
            command.count(" "),
            command,
        ),
    )
