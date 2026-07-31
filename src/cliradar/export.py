from __future__ import annotations

from typing import Any

import yaml

from .models import Catalog, CommandEntry


class _CompactDumper(yaml.SafeDumper):
    """Renders empty leaves as bare keys instead of explicit nulls."""


_CompactDumper.add_representer(
    type(None),
    lambda dumper, _: dumper.represent_scalar("tag:yaml.org,2002:null", ""),
)


def _selected(catalog: Catalog) -> list[CommandEntry]:
    if catalog.mode == "docs":
        entries = [entry for entry in catalog.commands.values() if entry.documented]
    else:
        entries = [entry for entry in catalog.commands.values() if entry.on_device]
    return sorted(entries, key=lambda entry: entry.command)


def render_tree_yaml(catalog: Catalog) -> str:
    tree: dict[str, Any] = {}
    for entry in _selected(catalog):
        node = tree
        for token in entry.command.split():
            node = node.setdefault(token, {})

    def compact(node: dict[str, Any]) -> dict[str, Any] | None:
        return {key: compact(value) for key, value in node.items()} or None

    def count(node: dict[str, Any]) -> int:
        return len(node) + sum(count(value) for value in node.values())

    source = "документации" if catalog.mode == "docs" else "устройства"
    header = (
        f"# CLI-команды {source} — единый каталог, сгенерирован CLIRadar"
        f" (режим {catalog.mode}).\n"
        "# Структура: вложенное дерево, ключ узла = добавляемое слово; лист = пусто.\n"
        f"# Корневых команд: {len(tree)}; всего узлов: {count(tree)}.\n"
    )
    body = yaml.dump(
        compact(tree) or {},
        Dumper=_CompactDumper,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
    )
    return header + body


def render_human_yaml(catalog: Catalog) -> str:
    entries = _selected(catalog)
    mapping = {entry.command: entry.description or None for entry in entries}
    source = "документации" if catalog.mode == "docs" else "устройства"
    header = (
        f"# Команды {source} и их назначение — сгенерировано CLIRadar"
        f" (режим {catalog.mode}).\n"
        "# Формат: команда: что она делает.\n"
        f"# Всего команд: {len(mapping)}.\n"
    )
    body = yaml.dump(
        mapping,
        Dumper=_CompactDumper,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
        width=1000,
    )
    return header + body
