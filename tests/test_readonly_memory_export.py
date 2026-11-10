from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ombrebrain.storage.backup_archive import (
    BackupArchiveError,
    build_readonly_text_export_page,
)


def _write_export_fixture(vault: Path) -> dict[str, bytes]:
    active = vault / "dynamic" / "general" / "memory_active.md"
    archived = vault / "archive" / "memory_archived.md"
    active.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)

    active_bytes = ("---\nid: active-1\ntags: [迁移]\n---\n" + "活跃记忆。" * 900).encode()
    archived_bytes = ("---\nid: archived-1\n---\n" + "归档记忆。" * 180).encode()
    active.write_bytes(active_bytes)
    archived.write_bytes(archived_bytes)

    source_bytes = ("原文证据第一行。\n" + "原文证据。" * 300).encode()
    source_ref = "src_" + hashlib.sha256(source_bytes).hexdigest()
    source = vault / "_sources" / f"{source_ref}.source"
    source.parent.mkdir(parents=True)
    source.write_bytes(source_bytes)

    return {
        "buckets/dynamic/general/memory_active.md": active_bytes,
        "buckets/archive/memory_archived.md": archived_bytes,
        f"sources/{source_ref}.source": source_bytes,
    }


def _read_every_page(vault: Path) -> tuple[list[dict], dict[str, bytes]]:
    cursor = ""
    pages: list[dict] = []
    reconstructed: dict[str, bytearray] = {}
    while True:
        page = build_readonly_text_export_page(
            str(vault), cursor=cursor, max_bytes=1024
        )
        pages.append(page)
        assert page["readonly"] is True
        assert page["page_raw_bytes"] <= 1024
        for item in page["items"]:
            current = reconstructed.setdefault(item["path"], bytearray())
            assert item["byte_offset"] == len(current)
            current.extend(item["content"].encode("utf-8"))
        if page["complete"]:
            assert page["next_cursor"] == ""
            break
        cursor = page["next_cursor"]
        assert cursor
    return pages, {path: bytes(data) for path, data in reconstructed.items()}


def test_readonly_export_pages_reconstruct_exact_active_archive_and_source(tmp_path):
    vault = tmp_path / "vault"
    expected = _write_export_fixture(vault)

    pages, reconstructed = _read_every_page(vault)

    assert reconstructed == expected
    assert len(pages) > 1
    assert {page["snapshot_id"] for page in pages} == {pages[0]["snapshot_id"]}
    first = pages[0]
    assert first["total_files"] == 3
    assert first["memory_files"] == 2
    assert first["archive_memory_files"] == 1
    assert first["source_evidence_files"] == 1

    seen = {
        item["path"]: item
        for page in pages
        for item in page["items"]
        if item["complete_file"]
    }
    for path, raw in expected.items():
        assert seen[path]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_readonly_export_does_not_change_vault_files(tmp_path):
    vault = tmp_path / "vault"
    expected = _write_export_fixture(vault)
    disk_paths = {
        relative: vault / relative.removeprefix("buckets/")
        if relative.startswith("buckets/")
        else vault / "_sources" / Path(relative).name
        for relative in expected
    }
    before = {
        name: (path.read_bytes(), path.stat().st_mtime_ns)
        for name, path in disk_paths.items()
    }

    _read_every_page(vault)

    after = {
        name: (path.read_bytes(), path.stat().st_mtime_ns)
        for name, path in disk_paths.items()
    }
    assert after == before


def test_readonly_export_starts_a_new_page_when_next_utf8_character_does_not_fit(
    tmp_path,
):
    vault = tmp_path / "vault"
    first = vault / "dynamic" / "general" / "a.md"
    second = vault / "dynamic" / "general" / "b.md"
    first.parent.mkdir(parents=True)
    first_bytes = b"a" * 1023
    second_bytes = "\u4e2d".encode("utf-8")
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)

    pages, reconstructed = _read_every_page(vault)

    assert reconstructed == {
        "buckets/dynamic/general/a.md": first_bytes,
        "buckets/dynamic/general/b.md": second_bytes,
    }
    assert len(pages) == 2
    assert pages[0]["page_raw_bytes"] == 1023
    assert pages[0]["complete"] is False


def test_readonly_export_emits_empty_files(tmp_path):
    vault = tmp_path / "vault"
    empty = vault / "dynamic" / "general" / "empty.md"
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"")

    pages, reconstructed = _read_every_page(vault)

    path = "buckets/dynamic/general/empty.md"
    assert reconstructed == {path: b""}
    assert pages[0]["total_files"] == 1
    assert pages[0]["items"] == [
        {
            "path": path,
            "kind": "memory_markdown",
            "archived": False,
            "size": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "byte_offset": 0,
            "byte_length": 0,
            "complete_file": True,
            "content": "",
        }
    ]


def test_readonly_export_rejects_cursor_after_source_changes(tmp_path):
    vault = tmp_path / "vault"
    _write_export_fixture(vault)
    first = build_readonly_text_export_page(str(vault), max_bytes=1024)
    assert first["next_cursor"]

    path = vault / "dynamic" / "general" / "memory_active.md"
    path.write_text(path.read_text(encoding="utf-8") + "后来新增。", encoding="utf-8")

    with pytest.raises(BackupArchiveError, match="内容发生变化"):
        build_readonly_text_export_page(
            str(vault), cursor=first["next_cursor"], max_bytes=1024
        )


@pytest.mark.parametrize("max_bytes", [0, 1023, 262145, True, "4096"])
def test_readonly_export_rejects_unsafe_page_sizes(tmp_path, max_bytes):
    vault = tmp_path / "vault"
    _write_export_fixture(vault)
    with pytest.raises(BackupArchiveError, match="max_bytes"):
        build_readonly_text_export_page(str(vault), max_bytes=max_bytes)


def test_readonly_export_rejects_invalid_cursor(tmp_path):
    vault = tmp_path / "vault"
    _write_export_fixture(vault)
    with pytest.raises(BackupArchiveError, match="cursor"):
        build_readonly_text_export_page(
            str(vault), cursor="not-a-cursor", max_bytes=1024
        )


@pytest.mark.asyncio
async def test_mcp_export_adapter_returns_the_verified_page(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    expected = _write_export_fixture(vault)

    from tools import _runtime, memory_export

    monkeypatch.setattr(_runtime, "config", {"buckets_dir": str(vault)})
    payload = json.loads(await memory_export.page(max_bytes=1024))

    assert payload["readonly"] is True
    assert payload["total_files"] == len(expected)
    assert payload["items"]
    assert payload["next_cursor"]
