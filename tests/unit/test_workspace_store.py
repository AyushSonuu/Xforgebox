"""Tests for :class:`forge.storage.workspace_store.WorkspaceStore`."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.errors import WorkspaceError
from forge.storage.workspace_store import FORGE_META_DIR, WorkspaceStore


def test_create_and_delete_roundtrip(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    d = store.create("ws_a")
    assert d.is_dir()
    assert (d / FORGE_META_DIR / "exec").is_dir()
    assert store.exists("ws_a")
    store.delete("ws_a")
    assert not store.exists("ws_a")


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    store.delete("nope")  # doesn't raise


def test_double_create_raises(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    store.create("ws_a")
    with pytest.raises(WorkspaceError):
        store.create("ws_a")


def test_path_returns_host_path(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    p = store.path("ws_b")
    assert p == (tmp_path / "workspaces" / "ws_b").resolve()
    assert not p.exists()


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b"])
def test_invalid_ids_rejected(tmp_path: Path, bad: str) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    with pytest.raises(WorkspaceError):
        store.create(bad)
