"""Integration tests for :class:`forge.drivers.docker_driver.DockerDriver`.

Skipped when the docker daemon is unreachable. Requires ``FORGE_TEST_IMAGE``
(defaults to ``python:3.14-slim``) to be pullable.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forge.config import ForgeConfig
from forge.drivers.base import Mount
from forge.drivers.docker_driver import DockerDriver
from forge.storage.workspace_store import WorkspaceStore
from tests.integration.conftest import FORGE_TEST_IMAGE

pytestmark = pytest.mark.integration


async def _make_env(
    driver: DockerDriver, config: ForgeConfig, image: str = FORGE_TEST_IMAGE
):
    """Create one pooled environment with the standard workspace mount."""
    return await driver.create_environment(
        image=image,
        mounts=[
            Mount(
                source=str(config.workspaces_root),
                target="/workspaces",
                read_only=False,
            )
        ],
        resources=None,
    )


async def test_capabilities_advertise_container_isolation(
    docker_driver: DockerDriver,
) -> None:
    caps = docker_driver.capabilities()
    assert caps.isolation == "container"
    assert caps.supports_streaming_logs is True


async def test_exec_prints_hello_and_exits_zero(
    docker_driver: DockerDriver, forge_config: ForgeConfig, tmp_path: Path
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_hello")
    env = await _make_env(docker_driver, forge_config)
    try:
        result = await docker_driver.exec_in_environment(
            env.id,
            ["python", "-c", "print('hello from forge')"],
            workspace_id="ws_hello",
            env=None,
            timeout_seconds=30.0,
            max_output_bytes=10_000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    assert result.exit_code == 0
    assert result.output.strip() == "hello from forge"
    assert result.truncated is False


async def test_second_exec_sees_files_from_first(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_persist")
    env = await _make_env(docker_driver, forge_config)
    try:
        write = await docker_driver.exec_in_environment(
            env.id,
            ["sh", "-c", "echo 'kept' > note.txt"],
            workspace_id="ws_persist",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1_000,
        )
        assert write.exit_code == 0
        read = await docker_driver.exec_in_environment(
            env.id,
            ["cat", "note.txt"],
            workspace_id="ws_persist",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1_000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    assert read.exit_code == 0
    assert read.output.strip() == "kept"


async def test_isolation_across_workspaces_on_same_container(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    """Two workspaces sharing one container each see only their own files."""
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_alpha")
    ws_store.create("ws_beta")
    env = await _make_env(docker_driver, forge_config)
    try:
        await docker_driver.exec_in_environment(
            env.id,
            ["sh", "-c", "echo alpha > only-alpha.txt"],
            workspace_id="ws_alpha",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1_000,
        )
        # From beta's cwd, alpha's file must NOT be visible.
        listing = await docker_driver.exec_in_environment(
            env.id,
            ["ls"],
            workspace_id="ws_beta",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1_000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    assert "only-alpha.txt" not in listing.output


async def test_exec_command_failure_returns_nonzero_no_raise(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_fail")
    env = await _make_env(docker_driver, forge_config)
    try:
        result = await docker_driver.exec_in_environment(
            env.id,
            ["python", "-c", "import sys; sys.exit(7)"],
            workspace_id="ws_fail",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1_000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    assert result.exit_code == 7


async def test_exec_timeout_marked(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_slow")
    env = await _make_env(docker_driver, forge_config)
    try:
        result = await docker_driver.exec_in_environment(
            env.id,
            ["sleep", "5"],
            workspace_id="ws_slow",
            env=None,
            timeout_seconds=1.0,
            max_output_bytes=1_000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    # 124 is our sentinel exit code for timeouts, matching GNU timeout.
    assert result.exit_code == 124


async def test_health_check_true_on_live_container(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    env = await _make_env(docker_driver, forge_config)
    try:
        assert await docker_driver.health_check(env.id) is True
    finally:
        await docker_driver.destroy_environment(env.id)


async def test_health_check_false_on_missing_container(
    docker_driver: DockerDriver,
) -> None:
    assert await docker_driver.health_check("no-such-container-id") is False


async def test_output_truncation(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_big")
    env = await _make_env(docker_driver, forge_config)
    try:
        result = await docker_driver.exec_in_environment(
            env.id,
            ["python", "-c", "print('x' * 5000)"],
            workspace_id="ws_big",
            env=None,
            timeout_seconds=15.0,
            max_output_bytes=1000,
        )
    finally:
        await docker_driver.destroy_environment(env.id)
    assert result.truncated is True
    assert len(result.output) <= 1000


async def test_destroy_is_idempotent(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    env = await _make_env(docker_driver, forge_config)
    await docker_driver.destroy_environment(env.id)
    await docker_driver.destroy_environment(env.id)


async def test_forge_run_reports_missing_workspace_dir(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    """If exec bypasses the driver env plumbing, forge-run should refuse."""
    env = await _make_env(docker_driver, forge_config)
    try:
        # Reach into the raw exec path (skip workspace_id → FORGE_WORKSPACE_DIR
        # is still injected). Instead, unset it via env override.
        container = docker_driver._docker.containers.container(env.id)  # noqa: SLF001
        exec_ = await container.exec(
            cmd=["forge-run", "true"],
            stdout=True,
            stderr=True,
            environment=["FORGE_WORKSPACE_DIR="],
        )
        async with exec_.start(detach=False) as stream:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
        info = await exec_.inspect()
        assert info["ExitCode"] == 64
    finally:
        await docker_driver.destroy_environment(env.id)


# Small hygiene test to keep asyncio warnings quiet in the debugger.
async def test_can_run_many_execs_serially(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> None:
    ws_store = WorkspaceStore(forge_config.workspaces_root)
    ws_store.create("ws_burst")
    env = await _make_env(docker_driver, forge_config)
    try:
        for i in range(5):
            r = await docker_driver.exec_in_environment(
                env.id,
                ["sh", "-c", f"echo {i}"],
                workspace_id="ws_burst",
                env=None,
                timeout_seconds=15.0,
                max_output_bytes=200,
            )
            assert r.exit_code == 0
            assert r.output.strip() == str(i)
    finally:
        await docker_driver.destroy_environment(env.id)


# Give the event loop a moment to close aiodocker's underlying aiohttp session
# between tests when running in-process. Without this the loop occasionally
# logs "unclosed transport" warnings which trip strict CI.
@pytest.fixture(autouse=True)
async def _tick_loop() -> None:
    yield
    await asyncio.sleep(0)
