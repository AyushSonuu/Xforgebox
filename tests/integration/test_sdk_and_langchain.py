"""Integration tests exercising the Python SDK end-to-end.

Runs both transports (LocalClient in-process, HTTPClient over an httpx
ASGI transport) through the same set of scenarios. This is the money test
for the MVP — it proves the SDK, LangChain adapter, and the daemon share
one behaviour surface.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from forge.client import Forge
from forge.client.http_client import HTTPClient
from forge.config import ForgeConfig
from forge.drivers.docker_driver import DockerDriver
from forge.langchain import ForgeSandbox
from forge.models import PoolConfig
from forge.server.app import create_app
from tests.integration.conftest import FORGE_TEST_IMAGE

pytestmark = pytest.mark.integration


def _tight_pool(cfg: ForgeConfig) -> ForgeConfig:
    cfg.default_pool = PoolConfig(
        image=FORGE_TEST_IMAGE,
        min_idle=1,
        max_size=4,
        idle_ttl_seconds=30,
        exec_timeout_seconds=30,
        max_output_bytes=100_000,
        lease_wait_timeout_seconds=15.0,
    )
    return cfg


@pytest_asyncio.fixture
async def local_forge(
    docker_driver: DockerDriver, forge_config: ForgeConfig
) -> AsyncIterator[Forge]:
    _tight_pool(forge_config)
    forge = Forge.local(config=forge_config, driver=docker_driver)
    async with forge:
        yield forge


@pytest_asyncio.fixture
async def http_forge(
    docker_driver: DockerDriver, forge_config: ForgeConfig, tmp_path: Path
) -> AsyncIterator[Forge]:
    _tight_pool(forge_config)
    app = create_app(config=forge_config, driver=docker_driver)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        # Swap out the SDK's transport for one pointed at the ASGI app.
        forge = Forge.__new__(Forge)
        client = HTTPClient("http://forge.local")
        client._client = httpx.AsyncClient(  # noqa: SLF001
            transport=transport, base_url="http://forge.local", timeout=30.0
        )
        forge._transport = client  # type: ignore[attr-defined]
        # rebuild _Workspaces with our transport
        from forge.client.sdk import _Workspaces
        forge.workspaces = _Workspaces(client)  # type: ignore[attr-defined]
        try:
            yield forge
        finally:
            await forge.close()


# ---------------------------------------------------------------------------
# SDK — LocalClient
# ---------------------------------------------------------------------------


async def test_local_sdk_exec_roundtrip(local_forge: Forge) -> None:
    ws = await local_forge.workspaces.create(image=FORGE_TEST_IMAGE, name="local-1")
    await ws.files.write("main.py", "print('local hi')\n")
    result = await ws.exec(["python", "main.py"])
    assert result.exit_code == 0
    assert result.output.strip() == "local hi"


async def test_local_sdk_snapshot_restore(local_forge: Forge) -> None:
    ws = await local_forge.workspaces.create(image=FORGE_TEST_IMAGE)
    await ws.files.write("hello.txt", "kept")
    snap = await ws.snapshots.create(name="v1")
    restored = await local_forge._transport.restore_snapshot(  # noqa: SLF001
        snap.id, name="restored", metadata={}
    )
    handle = await local_forge.workspaces.get(restored.id)
    r = await handle.files.read("hello.txt")
    assert r.content == "kept"


# ---------------------------------------------------------------------------
# SDK — HTTPClient (via ASGI transport)
# ---------------------------------------------------------------------------


async def test_http_sdk_exec_roundtrip(http_forge: Forge) -> None:
    ws = await http_forge.workspaces.create(image=FORGE_TEST_IMAGE, name="http-1")
    await ws.files.write("main.py", "print('http hi')\n")
    result = await ws.exec(["python", "main.py"])
    assert result.exit_code == 0
    assert result.output.strip() == "http hi"


async def test_http_sdk_pool_status(http_forge: Forge) -> None:
    stats = await http_forge.pool_status()
    assert isinstance(stats, list)


# ---------------------------------------------------------------------------
# LangChain adapter
# ---------------------------------------------------------------------------


async def test_forge_sandbox_end_to_end(local_forge: Forge) -> None:
    sandbox = await ForgeSandbox.afrom_thread(
        forge=local_forge, thread_id="t-42", image=FORGE_TEST_IMAGE
    )

    # Write + execute + read back via the sandbox API.
    write_result = await sandbox.awrite("main.py", "print('sandbox hi')\n")
    assert write_result.error is None
    result = await sandbox.aexecute("python main.py")
    assert result.exit_code == 0
    assert result.output.strip() == "sandbox hi"

    read_result = await sandbox.aread("main.py")
    assert read_result.error is None
    assert read_result.file_data is not None
    assert "sandbox hi" in read_result.file_data["content"]

    # Command failure does not raise — exit_code carries the signal.
    failed = await sandbox.aexecute("python -c 'import sys; sys.exit(3)'")
    assert failed.exit_code == 3

    # ls / grep exercised via BaseSandbox defaults.
    listing = await sandbox.als(".")
    assert listing.entries is not None
    names = {e["path"].split("/")[-1] for e in listing.entries}
    assert "main.py" in names

    grep_result = await sandbox.agrep("sandbox")
    assert grep_result.matches is not None
    assert any(m["path"].endswith("main.py") for m in grep_result.matches)


async def test_forge_sandbox_upload_download(local_forge: Forge) -> None:
    sandbox = await ForgeSandbox.afrom_thread(
        forge=local_forge, thread_id="t-upload", image=FORGE_TEST_IMAGE
    )
    up = await sandbox.aupload_files([("data.bin", b"raw\x00bytes")])
    assert up[0].error is None
    down = await sandbox.adownload_files(["data.bin"])
    assert down[0].error is None
    assert down[0].content == b"raw\x00bytes"


# ---------------------------------------------------------------------------
# Resource-sharing demo (light version)
# ---------------------------------------------------------------------------


async def test_many_agents_share_pool(local_forge: Forge) -> None:
    """5 agents × 3 execs each; pool is max_size=4; no exceptions."""
    import asyncio

    async def agent(i: int) -> int:
        ws = await local_forge.workspaces.create(image=FORGE_TEST_IMAGE)
        rcs = []
        for j in range(3):
            r = await ws.exec(["sh", "-c", f"echo {i}-{j}"])
            rcs.append(r.exit_code)
        return sum(rcs)

    results = await asyncio.gather(*(agent(i) for i in range(5)))
    assert all(r == 0 for r in results)

    stats = await local_forge.pool_status()
    top = stats[0]
    # Pool remains bounded.
    assert top.total <= 4
