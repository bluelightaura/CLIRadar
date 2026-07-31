from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class HelpOption:
    token: str
    description: str
    kind: str = "keyword"

    @property
    def executable(self) -> bool:
        return self.kind == "cr"


@dataclass
class CommandEntry:
    command: str
    description: str = ""
    source: set[str] = field(default_factory=set)
    executable: bool = False
    children: list[str] = field(default_factory=list)

    @property
    def documented(self) -> bool:
        return any(item.startswith("documentation:") for item in self.source)

    @property
    def on_device(self) -> bool:
        return "cli" in self.source

    def to_dict(
        self,
        *,
        mode: str,
        scan_complete: bool,
        provably_absent: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "command": self.command,
            "description": self.description,
            "executable": self.executable,
            "source": sorted(self.source),
        }
        if mode == "compare":
            if self.documented and self.on_device:
                result["comparison_status"] = "matched"
            elif self.on_device:
                result["comparison_status"] = "undocumented"
            elif scan_complete or provably_absent:
                result["comparison_status"] = "missing_on_device"
            else:
                result["comparison_status"] = "not_observed"
        elif mode == "audit" and self.on_device:
            result["device_status"] = "present"
        elif mode == "docs" and self.documented:
            result["documentation_status"] = "parsed"
        if self.children:
            result["children"] = sorted(self.children)
        return result


@dataclass
class Catalog:
    device: dict[str, Any]
    commands: dict[str, CommandEntry] = field(default_factory=dict)
    mode: str = "audit"
    scan: dict[str, Any] = field(default_factory=dict)
    # Command prefixes whose contextual help was read in full ("" is the root).
    # Every keyword a node offers is catalogued before any policy can skip it,
    # so a keyword absent from an enumerated node is absent from the device -
    # even when the scan as a whole stopped short somewhere else.
    enumerated: set[str] = field(default_factory=set)

    def provably_absent(self, command: str) -> bool:
        """Did the device get asked a question that would have revealed this?

        Descend the command one token at a time for as long as the scan stood
        on an enumerated node. The first token that node did not list is the
        proof: the device was asked and did not offer it. Once the walk leaves
        enumerated ground nothing further can be claimed - an ancestor's
        keyword list says nothing about what lies below it.
        """
        prefix = ""
        tokens = command.split()
        for index, token in enumerate(tokens):
            if prefix not in self.enumerated:
                return False
            child = " ".join(tokens[: index + 1])
            entry = self.commands.get(child)
            if entry is None or not entry.on_device:
                return True
            prefix = child
        return False

    def add(self, command: str, description: str, source: str) -> CommandEntry:
        normalized = " ".join(command.split())
        entry = self.commands.setdefault(normalized, CommandEntry(command=normalized))
        entry.source.add(source)
        if description and not entry.description:
            entry.description = description.strip()
        return entry

    def to_dict(self) -> dict[str, Any]:
        scan_complete = bool(self.scan.get("complete"))
        if self.mode == "compare":
            status_counts = {
                "matched": 0,
                "undocumented": 0,
                "missing_on_device": 0,
                "not_observed": 0,
            }
        elif self.mode == "audit":
            status_counts = {"present": 0}
        else:
            status_counts = {"parsed": 0}
        command_items = []
        for key in sorted(self.commands, key=lambda item: (item.count(" "), item)):
            item = self.commands[key].to_dict(
                mode=self.mode,
                scan_complete=scan_complete,
                provably_absent=self.provably_absent(key),
            )
            status = (
                item.get("comparison_status")
                or item.get("device_status")
                or item.get("documentation_status")
            )
            if status:
                status_counts[str(status)] = status_counts.get(str(status), 0) + 1
            command_items.append(item)

        summary: dict[str, Any] = {}
        if self.mode in {"compare", "audit"}:
            summary["device_commands"] = sum(
                entry.on_device for entry in self.commands.values()
            )
        if self.mode in {"compare", "docs"}:
            summary["documentation_commands"] = sum(
                entry.documented for entry in self.commands.values()
            )
        summary.update(status_counts)

        result = {
            "schema_version": 3,
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": self.mode,
        }
        if self.mode != "docs":
            result["device"] = self.device
        result.update(
            {
                "scan": self.scan,
                "summary": summary,
                "commands": command_items,
            }
        )
        return result
