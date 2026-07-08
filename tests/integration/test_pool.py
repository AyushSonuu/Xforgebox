"""Integration tests for :class:`forge.pool.container_pool.ContainerPool`.

Exercises the acquire/release cycle, warm start, isolation across workspaces
on shared containers, reaper behaviour, and health-kill recovery. Skipped
when the docker daemon is unreachable.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiodocker
import pytest
import pytest_asyncio

from forge.config import ForgeConfig
from forge.drivers.docker_driver import DockerDriver
from forge.models import PoolConfig
from forge.pool.container_pool import ContainerPool
from forge.storage.workspace_store import WorkspaceStore
from tests.integration.conftest import FORGE_TEST_IMAGE

pytestmark = pytest.mark.integration


@pytest.fixture
def pool_config(forge_config: ForgeConfig) -> ForgeConfig:
    """A config with tight pool bounds and short TTLs — the tests here rely on it."""
    forge_config.default_pool = PoolConfig(
        image=FORGE_TEST_IMAGE,
        min_idle=1,
        max_size=4,
        idle_ttl_seconds=3,
        exec_timeout_seconds=30,
        max_output_bytes=10_000,
        lease_wait_timeout_seconds=15.0,
    )
    return forge_config


@pytest_asyncio.fixture
async def pool(
    docker_driver: DockerDriver, pool_config: ForgeConfig
) -> AsyncIterator[ContainerPool]:
    p = ContainerPool(driver=docker_driver, config=pool_config)
    await p.start()
    try:
        yield p
    finally:
        await p.shutdown()


def _count_forge_containers(client: aiodocker.Docker) -> int:
    """Count running containers labelled by DockerDriver."""

    async def _inner() -> int:
        info = await client.containers.list(
            filters={"label": ["forge.managed-by=forge"]}
        )
        return len(info)

    return asyncio.get_event_loop().run_until_complete(_inner())


async def test_warm_start_reaches_min_idle(pool: ContainerPool) -> None:
    stats = pool.stats(FORGE_TEST_IMAGE)[0]
    # Warm-up runs synchronously in start(); one env should be idle now.
    assert stats.idle >= 1
    assert stats.in_use == 0


async def test_serial_sessions_reuse_pooled_container(
    pool: ContainerPool, pool_config: ForgeConfig
) -> None:
    """Five serial sessions should reuse the same warm env."""
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    ws_store.create("ws_serial")
    seen_env_ids: set[str] = set()
    for i in range(5):
        async with pool.session(
            workspace_id="ws_serial", image=FORGE_TEST_IMAGE
        ) as sess:
            r = await sess.exec(["sh", "-c", f"echo iter-{i}"])
            assert r.exit_code == 0
            seen_env_ids.add(sess._env.id)  # noqa: SLF001 — test-only introspection
    assert len(seen_env_ids) == 1  # one container reused


async def test_bounded_max_under_concurrency(
    pool: ContainerPool, pool_config: ForgeConfig, docker_client: aiodocker.Docker
) -> None:
    """20 concurrent sessions × max_size=4 must respect the pool's own bound.

    Two invariants:
    1. The pool's internal accounting never reports more slots than max_size.
    2. Wall-clock ``docker ps`` never exceeds ``max_size + 1`` — the +1 covers
       the brief window between a health-kill destroy and its replacement.
    """
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    for i in range(20):
        ws_store.create(f"ws_c{i}")

    docker_peak = 0
    pool_peak = 0
    observing = True

    async def observer() -> None:
        nonlocal docker_peak, pool_peak
        while observing:
            info = await docker_client.containers.list(
                filters={"label": ["forge.managed-by=forge"]}
            )
            docker_peak = max(docker_peak, len(info))
            stats = pool.stats(FORGE_TEST_IMAGE)[0]
            pool_peak = max(pool_peak, stats.total)
            await asyncio.sleep(0.03)

    async def worker(i: int) -> int:
        async with pool.session(
            workspace_id=f"ws_c{i}", image=FORGE_TEST_IMAGE
        ) as sess:
            r = await sess.exec(["sh", "-c", "sleep 0.15; echo ok"])
            return r.exit_code

    obs_task = asyncio.create_task(observer())
    try:
        results = await asyncio.gather(*(worker(i) for i in range(20)))
    finally:
        observing = False
        await obs_task

    assert all(rc == 0 for rc in results)
    # Pool-internal accounting is the true invariant.
    assert pool_peak <= 4, f"pool internal total peaked at {pool_peak}"
    # Wall-clock allows a one-container replacement window.
    assert docker_peak <= 5, f"docker ps peaked at {docker_peak}"


async def test_isolation_across_workspaces_via_pool(
    pool: ContainerPool, pool_config: ForgeConfig
) -> None:
    """Two workspaces on the same pooled container each see only their own tree."""
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    ws_store.create("ws_iso_a")
    ws_store.create("ws_iso_b")

    async with pool.session(
        workspace_id="ws_iso_a", image=FORGE_TEST_IMAGE
    ) as sess_a:
        await sess_a.exec(["sh", "-c", "echo alpha > alpha.txt"])
    async with pool.session(
        workspace_id="ws_iso_b", image=FORGE_TEST_IMAGE
    ) as sess_b:
        listing = await sess_b.exec(["ls"])
    assert "alpha.txt" not in listing.output


async def test_reaper_scales_back_to_min_idle(
    pool: ContainerPool, pool_config: ForgeConfig
) -> None:
    """Grow to max_size, then let idle_ttl_seconds pass — reaper trims back."""
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    for i in range(4):
        ws_store.create(f"ws_r{i}")

    async def worker(i: int) -> None:
        async with pool.session(
            workspace_id=f"ws_r{i}", image=FORGE_TEST_IMAGE
        ) as sess:
            await sess.exec(["true"])

    await asyncio.gather(*(worker(i) for i in range(4)))

    # 4 idle envs now. Idle TTL is 3s; reaper interval is 5s. Wait ~12s and
    # confirm we're back to min_idle=1.
    #
    # We shorten this in the test to keep suite fast by leaning on the fact
    # that _reaper_interval_s can be overridden.
    pool._reaper_interval_s = 0.5  # noqa: SLF001 — test-only
    await asyncio.sleep(6.0)

    stats = pool.stats(FORGE_TEST_IMAGE)[0]
    assert stats.total <= 2, f"expected trim to min_idle, saw {stats}"


async def test_health_kill_replaces_dead_container(
    pool: ContainerPool, pool_config: ForgeConfig, docker_client: aiodocker.Docker
) -> None:
    """Stop a pooled container out-of-band; next acquire should discard + replace."""
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    ws_store.create("ws_dead")

    # Drain to a known state: one warm env in the idle queue.
    async with pool.session(
        workspace_id="ws_dead", image=FORGE_TEST_IMAGE
    ) as sess:
        first_env_id = sess._env.id  # noqa: SLF001

    # Stop it behind the pool's back.
    container = docker_client.containers.container(first_env_id)
    await container.kill()

    # Next lease must yield a *different*, working container.
    async with pool.session(
        workspace_id="ws_dead", image=FORGE_TEST_IMAGE
    ) as sess:
        r = await sess.exec(["echo", "recovered"])
        assert r.exit_code == 0
        assert r.output.strip() == "recovered"
        assert sess._env.id != first_env_id  # noqa: SLF001

    stats = pool.stats(FORGE_TEST_IMAGE)[0]
    assert stats.total_health_kills >= 1


async def test_burst_two_execs_per_session(
    pool: ContainerPool, pool_config: ForgeConfig
) -> None:
    """A single session supports many execs (deep-agents tool-call bursts)."""
    ws_store = WorkspaceStore(pool_config.workspaces_root)
    ws_store.create("ws_burst")
    async with pool.session(
        workspace_id="ws_burst", image=FORGE_TEST_IMAGE
    ) as sess:
        r1 = await sess.exec(["sh", "-c", "echo one > note.txt"])
        r2 = await sess.exec(["cat", "note.txt"])
        r3 = await sess.exec(["sh", "-c", "echo two >> note.txt"])
        r4 = await sess.exec(["cat", "note.txt"])
    assert r1.exit_code == 0
    assert r2.output.strip() == "one"
    assert r3.exit_code == 0
    assert r4.output.strip().splitlines() == ["one", "two"]
