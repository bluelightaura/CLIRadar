"""What the documentation reader does with files that are not manuals.

A run points at a folder, and a folder holds whatever the operator put there:
a truncated download, a .docx that is a renamed PDF, a zip that unpacks to more
than the machine has. None of it may crash the reader or cost the run more than
the moment it takes to refuse, and every refusal has to say why - a manual that
gave nothing is otherwise indistinguishable from one that was read.

The hostile cases are here for a second reason. ``docparse.docx`` parses XML
from a file the operator chose, and bandit flags that as the shape of an XXE
attack. The suppression in that module claims the exposure is not real; these
tests are what backs the claim rather than asserting it.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from cliradar import docs
from cliradar.docs import scan_documentation


def _docx(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _scan(folder: Path) -> tuple[dict, list[str]]:
    reasons: list[str] = []
    found = scan_documentation(folder, on_skip=lambda _path, why: reasons.append(why))
    return found, reasons


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("a .docx that is not a zip", b"not a zip at all"),
        ("an empty file", b""),
        ("a zip holding no document", None),
        ("a document whose XML is truncated", None),
    ],
)
def test_a_broken_document_is_refused_with_a_reason(tmp_path: Path, name, payload) -> None:
    if payload is None:
        payload = (
            _docx({"readme.txt": "nothing here"})
            if "no document" in name
            else _docx({"word/document.xml": "<w:document><broken"})
        )
    (tmp_path / "manual.docx").write_bytes(payload)

    found, reasons = _scan(tmp_path)

    assert found == {}
    assert reasons and reasons[0].strip(), f"{name} was refused without saying why"


def test_an_archive_that_unpacks_past_the_limit_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A .docx is a zip, so its size on disk says nothing about the work reading
    # it costs. The declared size is checked before a byte is parsed.
    monkeypatch.setattr(docs, "MAX_PACKED_DOCUMENT_BYTES", 4096)
    (tmp_path / "manual.docx").write_bytes(_docx({"word/document.xml": "A" * 8192}))

    found, reasons = _scan(tmp_path)

    assert found == {}
    assert "превышает предел" in reasons[0]


def test_an_entity_bomb_is_refused_rather_than_expanded(tmp_path: Path) -> None:
    # "Billion laughs": ten nested entities, each ten copies of the last. An
    # expanding parser turns a few hundred bytes into gigabytes. This is the
    # case bandit's B314 is about, and the reader has to end it in a moment.
    entities = ['<!ENTITY e0 "xxxxxxxxxx">']
    entities += [f'<!ENTITY e{n} "' + f"&e{n - 1};" * 10 + '">' for n in range(1, 10)]
    bomb = "<!DOCTYPE d [" + "".join(entities) + "]><d>&e9;</d>"
    (tmp_path / "manual.docx").write_bytes(_docx({"word/document.xml": bomb}))

    found, reasons = _scan(tmp_path)

    assert found == {}
    assert reasons, "the bomb has to be refused, and the refusal has to be said"


def test_bytes_that_are_not_utf8_do_not_stop_the_read(tmp_path: Path) -> None:
    (tmp_path / "list.txt").write_bytes(b"show version\n\xff\xfe\x00binary\n")

    assert "show version" in scan_documentation(tmp_path)


def test_a_document_of_many_commands_is_read_whole(tmp_path: Path) -> None:
    listing = "\n".join(f"show test-{index}" for index in range(20_000))
    (tmp_path / "many.md").write_text(f"```text\n{listing}\n```\n", encoding="utf-8")

    assert len(scan_documentation(tmp_path)) == 20_000


def test_a_document_that_is_not_a_command_reference_yields_nothing(tmp_path: Path) -> None:
    # Ordinary technical prose with markdown headings. Nothing here is a
    # command, and the reader inventing some is worse than reading no file.
    (tmp_path / "textbook.md").write_text(
        "# Глава 1\n\n## Системы передачи\n\n"
        "Синхронная цифровая иерархия описывает уровни STM-1 и выше.\n\n"
        "### Выводы\n\nМетод применяется в транспортных сетях.\n",
        encoding="utf-8",
    )

    found, reasons = _scan(tmp_path)

    assert found == {}
    assert "не дал ни одной команды" in reasons[0]
