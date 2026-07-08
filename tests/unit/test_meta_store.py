"""Tests for :class:`forge.storage.meta_store.MetaStore`.

These use an on-disk temp DB (not ``:memory:``) so the WAL + foreign-key
PRAGMAs exercise the same code path as production.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge.errors import ConfigError, NotFoundError
from forge.models import (
    Artifact,
    Execution,
    Snapshot,
    Workspace,
    WorkspaceSpec,
)
from forge.storage.meta_store import MetaStore


@pytest.fixture
async def store(tmp_path: Path) -> MetaStore:
    ms = MetaStore(tmp_path / "meta.db")
    await ms.connect()
    try:
        yield ms
    finally:
        await ms.close()


def _mkworkspace(wid: str = "ws_1", name: str | None = None) -> Workspace:
    return Workspace(
        id=wid,
        spec=WorkspaceSpec(image="python:3.14-slim", env={"FOO": "bar"}),
        status="ready",
        name=name,
        metadata={"owner": "ash"},
    )


async def test_connect_is_idempotent(tmp_path: Path) -> None:
    ms = MetaStore(tmp_path / "meta.db")
    await ms.connect()
    await ms.connect()  # second call is a no-op
    await ms.close()


async def test_context_manager(tmp_path: Path) -> None:
    async with MetaStore(tmp_path / "m.db") as ms:
        await ms.create_workspace(_mkworkspace())
        got = await ms.get_workspace("ws_1")
        assert got.spec.image == "python:3.14-slim"


async def test_workspace_round_trip(store: MetaStore) -> None:
    ws = _mkworkspace(name="hello")
    await store.create_workspace(ws)
    got = await store.get_workspace(ws.id)
    assert got == ws


async def test_get_missing_workspace_raises(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.get_workspace("nope")


async def test_list_workspaces_filters_by_status(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace("ws_a"))
    b = _mkworkspace("ws_b")
    b = b.model_copy(update={"status": "deleted"})
    await store.create_workspace(b)
    ready = await store.list_workspaces(status="ready")
    assert [w.id for w in ready] == ["ws_a"]
    all_ws = await store.list_workspaces()
    assert {w.id for w in all_ws} == {"ws_a", "ws_b"}


async def test_update_workspace_status_missing(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.update_workspace_status("nope", "ready", datetime.now(UTC))


async def test_update_workspace_status(store: MetaStore) -> None:
    ws = _mkworkspace()
    await store.create_workspace(ws)
    new_ts = ws.updated_at + timedelta(seconds=5)
    await store.update_workspace_status(ws.id, "deleting", new_ts)
    got = await store.get_workspace(ws.id)
    assert got.status == "deleting"
    assert got.updated_at == new_ts


async def test_delete_workspace_cascades(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    ex = Execution(id="ex_1", workspace_id="ws_1", command=["true"])
    await store.create_execution(ex)
    await store.delete_workspace("ws_1")
    with pytest.raises(NotFoundError):
        await store.get_workspace("ws_1")
    with pytest.raises(NotFoundError):
        await store.get_execution("ex_1")


async def test_delete_missing_workspace_raises(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.delete_workspace("nope")


async def test_execution_round_trip(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    now = datetime.now(UTC)
    ex = Execution(
        id="ex_1",
        workspace_id="ws_1",
        environment_id="env_a",
        command=["python", "-c", "print(1)"],
        shell=False,
        status="succeeded",
        exit_code=0,
        truncated=False,
        output_path=None,
        started_at=now,
        finished_at=now + timedelta(milliseconds=5),
        idempotency_key="key-1",
    )
    await store.create_execution(ex)
    got = await store.get_execution(ex.id)
    assert got == ex


async def test_execution_idempotency_lookup(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    ex = Execution(
        id="ex_1",
        workspace_id="ws_1",
        command=["echo", "hi"],
        idempotency_key="k",
    )
    await store.create_execution(ex)
    got = await store.get_execution_by_idempotency("ws_1", "k")
    assert got is not None
    assert got.id == "ex_1"
    missing = await store.get_execution_by_idempotency("ws_1", "other")
    assert missing is None


async def test_update_execution_rejects_unknown_field(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    await store.create_execution(
        Execution(id="ex_1", workspace_id="ws_1", command=["true"])
    )
    with pytest.raises(ConfigError):
        await store.update_execution("ex_1", bogus="x")


async def test_update_execution_partial(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    await store.create_execution(
        Execution(id="ex_1", workspace_id="ws_1", command=["true"])
    )
    ts = datetime.now(UTC)
    await store.update_execution(
        "ex_1",
        status="succeeded",
        exit_code=0,
        truncated=True,
        finished_at=ts,
    )
    got = await store.get_execution("ex_1")
    assert got.status == "succeeded"
    assert got.exit_code == 0
    assert got.truncated is True
    assert got.finished_at == ts


async def test_update_execution_missing_raises(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.update_execution("nope", status="failed")


async def test_list_executions_by_workspace(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    for i in range(3):
        await store.create_execution(
            Execution(id=f"ex_{i}", workspace_id="ws_1", command=["true"])
        )
    xs = await store.list_executions("ws_1")
    assert [e.id for e in xs] == ["ex_0", "ex_1", "ex_2"]


async def test_snapshot_round_trip(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    s = Snapshot(id="snap_1", workspace_id="ws_1", name="v1", size_bytes=42)
    await store.create_snapshot(s)
    assert await store.get_snapshot("snap_1") == s
    assert await store.list_snapshots("ws_1") == [s]


async def test_get_missing_snapshot_raises(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.get_snapshot("nope")


async def test_artifact_round_trip(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    a = Artifact(
        id="art_1",
        workspace_id="ws_1",
        path="out.log",
        size_bytes=10,
        content_type="text/plain",
    )
    await store.create_artifact(a)
    assert await store.get_artifact("art_1") == a
    assert await store.list_artifacts("ws_1") == [a]


async def test_get_missing_artifact_raises(store: MetaStore) -> None:
    with pytest.raises(NotFoundError):
        await store.get_artifact("nope")


async def test_scope_binding(store: MetaStore) -> None:
    await store.create_workspace(_mkworkspace())
    assert await store.resolve_scope("thread", "t1") is None
    await store.bind_scope("thread", "t1", "ws_1")
    assert await store.resolve_scope("thread", "t1") == "ws_1"
    # Rebind moves it
    await store.create_workspace(_mkworkspace("ws_2"))
    await store.bind_scope("thread", "t1", "ws_2")
    assert await store.resolve_scope("thread", "t1") == "ws_2"


async def test_pool_events_are_ordered_newest_first(store: MetaStore) -> None:
    await store.append_pool_event("python:3.14-slim", "warm", {"n": 1})
    await store.append_pool_event("python:3.14-slim", "acquire", {"n": 2})
    await store.append_pool_event("other", "warm", {"n": 3})
    py = await store.list_pool_events("python:3.14-slim")
    assert [e["kind"] for e in py] == ["acquire", "warm"]
    everything = await store.list_pool_events()
    assert [e["payload"]["n"] for e in everything] == [3, 2, 1]


async def test_naive_datetime_refused(store: MetaStore) -> None:
    ws = _mkworkspace()
    with pytest.raises(ConfigError):
        await store.update_workspace_status(ws.id, "ready", datetime(2025, 1, 1))
