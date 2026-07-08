"""Tests for :class:`forge.services.workspace_service.WorkspaceService`."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge.errors import NotFoundError
from forge.models import WorkspaceSpec
from forge.services.workspace_service import WorkspaceService
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import WorkspaceStore


@pytest.fixture
async def service(tmp_path: Path):
    meta = MetaStore(tmp_path / "meta.db")
    await meta.connect()
    ws_store = WorkspaceStore(tmp_path / "workspaces")
    svc = WorkspaceService(meta, ws_store)
    try:
        yield svc, meta, ws_store
    finally:
        await meta.close()


async def test_create_persists_row_and_directory(service) -> None:
    svc, meta, ws_store = service
    spec = WorkspaceSpec(image="python:3.14-slim")
    ws = await svc.create(spec, name="demo", metadata={"owner": "alice"})
    assert ws.status == "ready"
    assert ws.name == "demo"
    assert ws_store.exists(ws.id)
    fetched = await meta.get_workspace(ws.id)
    assert fetched.id == ws.id
    assert fetched.spec == spec


async def test_get_missing_raises(service) -> None:
    svc, *_ = service
    with pytest.raises(NotFoundError):
        await svc.get("ws_missing")


async def test_delete_removes_row_and_dir(service) -> None:
    svc, meta, ws_store = service
    ws = await svc.create(WorkspaceSpec(image="python:3.14-slim"))
    await svc.delete(ws.id)
    assert not ws_store.exists(ws.id)
    with pytest.raises(NotFoundError):
        await meta.get_workspace(ws.id)


async def test_list_filters_by_status(service) -> None:
    svc, *_ = service
    a = await svc.create(WorkspaceSpec(image="python:3.14-slim"))
    b = await svc.create(WorkspaceSpec(image="python:3.14-slim"))
    ready = await svc.list(status="ready")
    ids = {w.id for w in ready}
    assert {a.id, b.id} <= ids
