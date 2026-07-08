"""Unit tests for snapshot store + service + artifact service.

These are pure-Python tests (no docker) so they run alongside the metastore
and files-service tests. The round-trip integrity test covers the primary
snapshot use case: snap workspace A -> restore into workspace B ->
byte-identical files.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from forge.errors import ArtifactError, PathEscapeError, SnapshotError
from forge.models import WorkspaceSpec
from forge.services.artifact_service import ArtifactService
from forge.services.snapshot_service import SnapshotService
from forge.services.workspace_service import WorkspaceService
from forge.storage.artifact_store import ArtifactStore
from forge.storage.meta_store import MetaStore
from forge.storage.snapshot_store import SnapshotStore
from forge.storage.workspace_store import WorkspaceStore


@pytest_asyncio.fixture
async def rig(tmp_path: Path) -> AsyncIterator[
    tuple[SnapshotService, ArtifactService, WorkspaceService, WorkspaceStore]
]:
    meta = MetaStore(tmp_path / "meta.db")
    await meta.connect()
    ws_store = WorkspaceStore(tmp_path / "workspaces")
    workspaces = WorkspaceService(meta, ws_store)
    snap_store = SnapshotStore(tmp_path / "snapshots")
    art_store = ArtifactStore(tmp_path / "artifacts")
    snap_svc = SnapshotService(
        meta=meta,
        workspaces=workspaces,
        workspace_store=ws_store,
        snapshots=snap_store,
    )
    art_svc = ArtifactService(
        meta=meta,
        workspaces=workspaces,
        workspace_store=ws_store,
        artifacts=art_store,
    )
    try:
        yield snap_svc, art_svc, workspaces, ws_store
    finally:
        await meta.close()


# ---------------------------------------------------------------------------
# Snapshot round-trip
# ---------------------------------------------------------------------------


async def test_snapshot_restore_roundtrip(rig) -> None:  # type: ignore[no-untyped-def]
    snap_svc, _art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    path = ws_store.path(ws.id)
    (path / "hello.txt").write_text("hi there")
    (path / "sub").mkdir()
    (path / "sub" / "nested.py").write_text("print('x')\n")

    snap = await snap_svc.create(workspace_id=ws.id, name="v1")
    assert snap.size_bytes > 0

    restored = await snap_svc.restore(snapshot_id=snap.id, name="from-snap")
    dest = ws_store.path(restored.id)
    assert (dest / "hello.txt").read_text() == "hi there"
    assert (dest / "sub" / "nested.py").read_text() == "print('x')\n"


async def test_snapshot_excludes_forge_exec_logs(rig) -> None:  # type: ignore[no-untyped-def]
    snap_svc, _art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    path = ws_store.path(ws.id)
    (path / "keep.txt").write_text("keep")
    (path / ".forge" / "exec").mkdir(parents=True, exist_ok=True)
    (path / ".forge" / "exec" / "old.log").write_text("junk")

    snap = await snap_svc.create(workspace_id=ws.id)
    restored = await snap_svc.restore(snapshot_id=snap.id)
    dest = ws_store.path(restored.id)
    assert (dest / "keep.txt").is_file()
    assert not (dest / ".forge" / "exec" / "old.log").exists()


async def test_snapshot_missing_raises_on_restore(rig) -> None:  # type: ignore[no-untyped-def]
    from forge.errors import NotFoundError

    snap_svc, *_ = rig
    with pytest.raises(NotFoundError):
        await snap_svc.restore(snapshot_id="snap_missing")


async def test_snapshot_delete_after_restore_ok(rig) -> None:  # type: ignore[no-untyped-def]
    snap_svc, _art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    (ws_store.path(ws.id) / "a.txt").write_text("a")
    snap = await snap_svc.create(workspace_id=ws.id)
    # Restore twice — makes sure we didn't consume the archive.
    ws1 = await snap_svc.restore(snapshot_id=snap.id)
    ws2 = await snap_svc.restore(snapshot_id=snap.id)
    assert ws1.id != ws2.id
    assert (ws_store.path(ws1.id) / "a.txt").read_text() == "a"
    assert (ws_store.path(ws2.id) / "a.txt").read_text() == "a"


async def test_snapshot_corrupt_archive_raises(rig, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    snap_svc, _art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    (ws_store.path(ws.id) / "a.txt").write_text("a")
    snap = await snap_svc.create(workspace_id=ws.id)
    # Corrupt the archive on disk.
    archive_path = tmp_path / "snapshots" / f"{snap.id}.tar.zst"
    archive_path.write_bytes(b"not a real archive")
    with pytest.raises(SnapshotError):
        await snap_svc.restore(snapshot_id=snap.id)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


async def test_artifact_export_and_read(rig) -> None:  # type: ignore[no-untyped-def]
    _snap, art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    (ws_store.path(ws.id) / "report.json").write_text('{"ok": true}')

    art_row = await art.export(workspace_id=ws.id, path="report.json")
    assert art_row.path == "report.json"
    assert art_row.size_bytes == len('{"ok": true}')
    assert art_row.content_type == "application/json"

    aio = await art.read(art_row.id)
    chunks: list[bytes] = []
    async for c in aio:
        chunks.append(c)
    assert b"".join(chunks) == b'{"ok": true}'


async def test_artifact_rejects_reserved_and_traversal(rig) -> None:  # type: ignore[no-untyped-def]
    _snap, art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    with pytest.raises(PathEscapeError):
        await art.export(workspace_id=ws.id, path=".forge/exec/junk.log")
    with pytest.raises(PathEscapeError):
        await art.export(workspace_id=ws.id, path="../secret")


async def test_artifact_missing_file_errors(rig) -> None:  # type: ignore[no-untyped-def]
    _snap, art, workspaces, _ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    with pytest.raises(ArtifactError):
        await art.export(workspace_id=ws.id, path="nope.txt")


async def test_artifact_list_by_workspace(rig) -> None:  # type: ignore[no-untyped-def]
    _snap, art, workspaces, ws_store = rig
    ws = await workspaces.create(WorkspaceSpec(image="python:3.14-slim"))
    (ws_store.path(ws.id) / "a.txt").write_text("a")
    (ws_store.path(ws.id) / "b.txt").write_text("b")
    a = await art.export(workspace_id=ws.id, path="a.txt")
    b = await art.export(workspace_id=ws.id, path="b.txt")
    listing = await art.list(ws.id)
    ids = {row.id for row in listing}
    assert {a.id, b.id} <= ids
