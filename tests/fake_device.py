"""Device emulator used by the navigator and mode-scan tests.

It reproduces the behaviour that breaks naive CLI crawlers: confirmation
dialogs, banners shaped like prompts, several modes sharing one prompt, a root
`exit` that ends the session, silent logout and hung commands.
"""

from __future__ import annotations

HELP_BY_MODE = {
    "": "  configure  Enter configuration mode\n  show  Display information\n  <cr>\n",
    # `logging` is a parameter-free statement: it takes effect the moment it is
    # typed and opens no context - the shape the safe probe policy must decline.
    "config": (
        "  vlan  VLAN view\n  interface  Interface view\n"
        "  hostname  Set hostname\n  logging  Enable logging\n"
    ),
    "config-if": "  ip  Interface address\n  <cr>\n",
}
# Contextual help for prefixes inside a mode. Entering a mode takes an
# argument, as it does on real platforms, so sampling is exercised too.
HELP_BY_PREFIX = {
    ("config", "vlan"): "  <1-4094>  VLAN identifier\n",
    ("config", "interface"): "  IFNAME  Interface name\n",
    ("config", "hostname"): "  WORD  New hostname\n",
}


class FakeDevice:
    """A switch that answers help, enters modes and misbehaves on demand."""

    def __init__(
        self,
        *,
        hostname: str = "SW1",
        banner: str = "",
        idle_logout_after: int | None = None,
        hang_commands: frozenset[str] = frozenset(),
        confirm_commands: frozenset[str] = frozenset(),
        drift_after: int | None = None,
        interrupt_exits_mode: bool = True,
        dead_channel_after: int | None = None,
        reset_on_command: str | None = None,
    ) -> None:
        self.hostname = hostname
        self.banner = banner
        self.idle_logout_after = idle_logout_after
        self.hang_commands = hang_commands
        self.confirm_commands = confirm_commands
        # Number of help queries after which the device silently falls back to
        # the root context, imitating a mode that times out on its own.
        self.drift_after = drift_after
        # Observed on real hardware: Ctrl-C leaves the configuration mode, so
        # using it as a position check would move the session while measuring.
        self.interrupt_exits_mode = interrupt_exits_mode
        self.interrupts = 0
        # The device hangs up: every operation raises until the channel is
        # rebuilt, the way a dropped SSH session behaves.
        self.dead_channel_after = dead_channel_after
        # A specific command that takes the session down when executed, the way
        # `flush arp all` reset a live switch by clearing its management ARP.
        self.reset_on_command = reset_on_command
        self.stack: list[str] = []
        self.commands: list[str] = []
        self.queries: list[str] = []
        self.reopens = 0
        self.hung = False
        self.closed = False
        self.executed_writes: list[str] = []

    @property
    def mode(self) -> str:
        return self.stack[-1] if self.stack else ""

    def prompt(self) -> str:
        suffix = f"({self.mode})" if self.mode else ""
        return f"{self.hostname}{suffix}#"

    def _maybe_logout(self) -> bool:
        """Drop the session once, the way an idle timeout does mid-scan."""
        if self.idle_logout_after is not None and len(self.commands) >= self.idle_logout_after:
            self.idle_logout_after = None
            self.stack.clear()
            self.closed = True
            return True
        return False

    def _maybe_drift(self) -> None:
        if self.drift_after is not None and len(self.queries) >= self.drift_after:
            self.drift_after = None
            self.stack.clear()

    def _check_channel(self) -> None:
        if self.dead_channel_after is not None and len(self.queries) >= self.dead_channel_after:
            raise OSError("Socket is closed")

    # -- terminal protocol ----------------------------------------------
    def query_help(self, prefix: str) -> str:
        self.queries.append(prefix)
        self._check_channel()
        if self.hung or self.closed:
            return ""
        self._maybe_drift()
        stripped = prefix.strip()
        if stripped:
            options = HELP_BY_PREFIX.get((self.mode, stripped), "  <cr>\n")
        elif self.mode.startswith("config-vlan"):
            options = "  name  Set VLAN name\n  <cr>\n"
        else:
            options = HELP_BY_MODE.get(self.mode, "  <cr>\n")
        return f"{prefix}?\n{options}{self.prompt()}"

    def run_command(self, command: str) -> str:
        self.commands.append(command)
        self._check_channel()
        if self.closed:
            return ""
        if command == self.reset_on_command:
            self.executed_writes.append(command)
            self.stack.clear()
            self.closed = True
            raise OSError("Connection reset by peer")
        if command in self.confirm_commands:
            # The scanner must decline; the device stays where it was.
            return f"{command}\nAre you sure? (y/n)"
        if command in self.hang_commands:
            self.hung = True
            return f"{command}\n"
        if self._maybe_logout():
            return "\nUser Access Verification\n\nUsername:"

        if command == "configure":
            self.stack.append("config")
        elif command.startswith("vlan ") and self.mode == "config":
            self.stack.append(f"config-vlan-{command.split()[1]}")
        elif command.startswith("interface ") and self.mode == "config":
            # Every interface kind shares one prompt, as real platforms do.
            self.stack.append("config-if")
        elif command in {"exit", "quit"}:
            if not self.stack:
                self.closed = True
                return ""
            self.stack.pop()
        elif command == "end":
            self.stack.clear()
        else:
            self.executed_writes.append(command)

        return f"{command}\n{self.banner}{self.prompt()}"

    def probe_prompt(self) -> str:
        """An empty line: redraws the prompt and changes nothing."""
        self._check_channel()
        if self.hung or self.closed:
            return ""
        return f"\n{self.prompt()}"

    def interrupt(self) -> str:
        self._check_channel()
        self.hung = False
        self.interrupts += 1
        if self.closed:
            return ""
        if self.interrupt_exits_mode and self.stack:
            self.stack.pop()
        return f"^C\n{self.prompt()}"

    def reopen(self) -> None:
        self.reopens += 1
        self.stack.clear()
        self.hung = False
        self.closed = False
        self.dead_channel_after = None
