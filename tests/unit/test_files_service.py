"""Tests for :class:`forge.services.files_service.FilesService`.

Focuses on:
- path escape rejection (absolute, ``..``, symlinks that leave the tree),
- round-trip semantics for read/write/edit/glob/grep/delete,
- Deep-Agents-style error paths (missing ``old_string``, duplicate matches).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from forge.errors import PathEscapeError, WorkspaceError
from forge.services.files_service import FilesService, UploadItem
from forge.storage.workspace_store import WorkspaceStore


@pytest.fixture
def svc(tmp_path: Path) -> FilesService:
    ws_root = tmp_path / "workspaces"
    store = WorkspaceStore(ws_root)
    store.create("ws_1")
    return FilesService(store)


@pytest.fixture
def ws_dir(tmp_path: Path) -> Path:
    return (tmp_path / "workspaces" / "ws_1").resolve()


# ---------------------------------------------------------------------------
# Path escape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/",
        "../etc/passwd",
        "a/../../etc/passwd",
        "./../../outside",
    ],
)
def test_read_rejects_traversal(svc: FilesService, bad: str) -> None:
    with pytest.raises(PathEscapeError):
        svc.read("ws_1", bad)


def test_write_rejects_absolute(svc: FilesService, tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        svc.write("ws_1", str(tmp_path / "escape.txt"), "nope")


def test_symlink_out_of_workspace_rejected(
    svc: FilesService, ws_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    link = ws_dir / "link.txt"
    os.symlink(outside, link)
    with pytest.raises(PathEscapeError):
        svc.read("ws_1", "link.txt")


def test_symlink_inside_workspace_ok(
    svc: FilesService, ws_dir: Path
) -> None:
    (ws_dir / "target.txt").write_text("hello")
    os.symlink(ws_dir / "target.txt", ws_dir / "alias.txt")
    content, _, _ = svc.read("ws_1", "alias.txt")
    assert content == "hello"


def test_reserved_dot_forge_dir_hidden(svc: FilesService, ws_dir: Path) -> None:
    (ws_dir / ".forge" / "exec" / "hidden.log").write_text("hidden")
    with pytest.raises(PathEscapeError):
        svc.read("ws_1", ".forge/exec/hidden.log")
    with pytest.raises(PathEscapeError):
        svc.write("ws_1", ".forge/something", "no")
    listing = svc.ls("ws_1")
    assert ".forge" not in {e.name for e in listing}


# ---------------------------------------------------------------------------
# Read / write / edit
# ---------------------------------------------------------------------------


def test_write_creates_parents_and_reads_back(svc: FilesService) -> None:
    svc.write("ws_1", "src/pkg/module.py", "print('hi')\n")
    content, total, trunc = svc.read("ws_1", "src/pkg/module.py")
    assert content == "print('hi')\n"
    assert total == 1
    assert trunc is False


def test_read_with_offset_limit(svc: FilesService) -> None:
    svc.write("ws_1", "big.txt", "\n".join(f"line-{i}" for i in range(100)) + "\n")
    content, total, trunc = svc.read("ws_1", "big.txt", offset=10, limit=5)
    lines = content.splitlines()
    assert lines == [f"line-{i}" for i in range(10, 15)]
    assert total == 100
    assert trunc is True


def test_read_missing_raises(svc: FilesService) -> None:
    with pytest.raises(WorkspaceError):
        svc.read("ws_1", "nope.txt")


def test_read_binary_file_rejected(svc: FilesService, ws_dir: Path) -> None:
    (ws_dir / "bin.dat").write_bytes(b"\x00\x01\x02\x03")
    with pytest.raises(WorkspaceError):
        svc.read("ws_1", "bin.dat")


def test_edit_single_occurrence(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "hello world")
    n = svc.edit("ws_1", "a.txt", "world", "there")
    assert n == 1
    content, _, _ = svc.read("ws_1", "a.txt")
    assert content == "hello there"


def test_edit_duplicate_without_replace_all_raises(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "hi hi hi")
    with pytest.raises(WorkspaceError):
        svc.edit("ws_1", "a.txt", "hi", "hey")


def test_edit_replace_all(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "hi hi hi")
    n = svc.edit("ws_1", "a.txt", "hi", "hey", replace_all=True)
    assert n == 3
    content, _, _ = svc.read("ws_1", "a.txt")
    assert content == "hey hey hey"


def test_edit_missing_old_string_raises(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "hello")
    with pytest.raises(WorkspaceError):
        svc.edit("ws_1", "a.txt", "world", "there")


# ---------------------------------------------------------------------------
# ls / glob / grep / delete
# ---------------------------------------------------------------------------


def test_ls_lists_children(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "a")
    svc.write("ws_1", "sub/b.txt", "b")
    entries = svc.ls("ws_1")
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"a.txt", "sub"}
    assert by_name["a.txt"].is_dir is False
    assert by_name["sub"].is_dir is True


def test_glob_simple(svc: FilesService) -> None:
    svc.write("ws_1", "a.py", "")
    svc.write("ws_1", "src/b.py", "")
    svc.write("ws_1", "src/c.txt", "")
    matches = svc.glob("ws_1", "**/*.py")
    assert set(matches) == {"a.py", "src/b.py"}


def test_grep_returns_matches(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "alpha\nbeta\ngamma\n")
    svc.write("ws_1", "b.txt", "beta only\n")
    hits = svc.grep("ws_1", r"beta")
    paths_and_lines = {(h.path, h.line) for h in hits}
    assert paths_and_lines == {("a.txt", 2), ("b.txt", 1)}


def test_grep_with_path_glob(svc: FilesService) -> None:
    svc.write("ws_1", "a.py", "match\n")
    svc.write("ws_1", "b.txt", "match\n")
    hits = svc.grep("ws_1", "match", path_glob="*.py")
    assert [h.path for h in hits] == ["a.py"]


def test_delete_file(svc: FilesService) -> None:
    svc.write("ws_1", "a.txt", "x")
    svc.delete("ws_1", "a.txt")
    with pytest.raises(WorkspaceError):
        svc.read("ws_1", "a.txt")


def test_delete_missing_is_noop(svc: FilesService) -> None:
    svc.delete("ws_1", "nope.txt")


def test_delete_directory_rejects_nonempty(svc: FilesService) -> None:
    svc.write("ws_1", "sub/a.txt", "a")
    with pytest.raises(WorkspaceError):
        svc.delete("ws_1", "sub")


def test_upload_and_download(svc: FilesService) -> None:
    items = [
        UploadItem(path="data/hello.bin", content_b64=base64.b64encode(b"hi\x00").decode()),
    ]
    written = svc.upload_files("ws_1", items)
    assert written == ["data/hello.bin"]
    dl = svc.download_files("ws_1", ["data/hello.bin"])
    assert base64.b64decode(dl[0].content_b64) == b"hi\x00"


def test_upload_rejects_bad_base64(svc: FilesService) -> None:
    with pytest.raises(WorkspaceError):
        svc.upload_files("ws_1", [UploadItem(path="a", content_b64="not!base64")])
