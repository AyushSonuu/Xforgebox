"""Integration tests for :class:`forge.services.execution_service.ExecutionService`.

Exercises real Docker + real pool + real workspaces, with the metastore on
a tmpdir SQLite DB.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from forge.config import ForgeConfig
from forge.drivers.docker_driver import DockerDriver
from forge.events.bus import EventBus
from forge.models import ExecRequest, PoolConfig, WorkspaceSpec
from forge.pool.container_pool import ContainerPool
from forge.services.execution_service import ExecutionService
from forge.services.workspace_service import WorkspaceService
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import WorkspaceStore
from tests.integration.conftest import FORGE_TEST_IMAGE

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def rig(
    docker_driver: DockerDriver, forge_config: ForgeConfig, tmp_path: Path
) -> AsyncIterator[tuple[ExecutionService, WorkspaceService, EventBus]]:
    """End-to-end fixture: metastore + workspace store + pool + services."""
    forge_config.default_pool = PoolConfig(
        image=FORGE_TEST_IMAGE,
        min_idle=1,
        max_size=4,
        idle_ttl_seconds=30,
        exec_timeout_seconds=30,
        max_output_bytes=100_000,
        lease_wait_timeout_seconds=15.0,
    )

    meta = MetaStore(forge_config.meta_db_path)
    await meta.connect()
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    workspaces = WorkspaceService(meta, ws_store)
    pool = ContainerPool(driver=docker_driver, config=forge_config)
    await pool.start()
    events = EventBus()
    svc = ExecutionService(
        meta=meta,
        workspaces=workspaces,
        workspace_store=ws_store,
        pool=pool,
        events=events,
    )
    try:
        yield svc, workspaces, events
    finally:
        await pool.shutdown()
        await meta.close()


async def test_single_exec_success(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    result = await svc.exec(ws.id, ExecRequest(command=["python", "-c", "print('hi')"]))
    assert result.exit_code == 0
    assert result.output.strip() == "hi"
    assert result.duration_ms >= 0
    # Row is persisted with terminal status.
    row = await svc.get(result.execution_id)
    assert row.status == "succeeded"
    assert row.exit_code == 0


async def test_three_sequential_execs_share_workspace(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    r1 = await svc.exec(ws.id, ExecRequest(command=["sh", "-c", "echo one > log.txt"]))
    r2 = await svc.exec(ws.id, ExecRequest(command=["sh", "-c", "cat log.txt"]))
    r3 = await svc.exec(ws.id, ExecRequest(command=["rm", "log.txt"]))
    assert r1.exit_code == r2.exit_code == r3.exit_code == 0
    assert r2.output.strip() == "one"


async def test_two_workspaces_stay_isolated(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    a = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    b = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    await svc.exec(a.id, ExecRequest(command=["sh", "-c", "echo alpha > only-a.txt"]))
    ls = await svc.exec(b.id, ExecRequest(command=["ls"]))
    assert "only-a.txt" not in ls.output


async def test_idempotency_key_short_circuits(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    req = ExecRequest(
        command=["sh", "-c", "date +%s%N"],
        idempotency_key="key-a",
    )
    first = await svc.exec(ws.id, req)
    # Second call with same key must NOT invoke the container again.
    second = await svc.exec(ws.id, req)
    assert first.execution_id == second.execution_id


async def test_command_failure_returns_nonzero_no_raise(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    result = await svc.exec(
        ws.id, ExecRequest(command=["python", "-c", "import sys; sys.exit(3)"])
    )
    assert result.exit_code == 3
    row = await svc.get(result.execution_id)
    assert row.status == "failed"


async def test_timeout_marked_status(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    result = await svc.exec(
        ws.id, ExecRequest(command=["sleep", "5"], timeout_seconds=1.0)
    )
    assert result.exit_code == 124
    row = await svc.get(result.execution_id)
    assert row.status == "timed_out"


async def test_oversized_output_truncates_and_spills(rig) -> None:  # type: ignore[no-untyped-def]
    svc, workspaces, _events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))
    result = await svc.exec(
        ws.id,
        ExecRequest(
            command=["python", "-c", "print('x' * 5000)"],
            max_output_bytes=200,
        ),
    )
    assert result.truncated is True
    assert len(result.output) <= 200
    assert result.output_path is not None
    assert result.output_path.startswith(".forge/exec/")


async def test_event_bus_emits_lifecycle(rig) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    svc, workspaces, events = rig
    ws = await workspaces.create(WorkspaceSpec(image=FORGE_TEST_IMAGE))

    seen: list[str] = []

    async def collect() -> None:
        async for ev in events.subscribe(workspace_id=ws.id):
            seen.append(ev.kind)
            if ev.kind == "execution.finished":
                return

    task = asyncio.create_task(collect())
    # Yield once so the subscribe generator is fully installed before publish.
    await asyncio.sleep(0.05)
    await svc.exec(ws.id, ExecRequest(command=["echo", "ok"]))
    await asyncio.wait_for(task, timeout=10.0)
    assert seen == ["execution.queued", "execution.started", "execution.finished"]
