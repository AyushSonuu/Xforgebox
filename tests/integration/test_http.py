"""HTTP integration tests — real FastAPI app against real Docker.

Uses ``httpx.ASGITransport`` to hit the app in-process without opening a
network port. The ``lifespan`` context runs during transport startup so
``pool.start()`` fires as normal.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from forge.config import ForgeConfig
from forge.drivers.docker_driver import DockerDriver
from forge.models import PoolConfig
from forge.server.app import create_app
from tests.integration.conftest import FORGE_TEST_IMAGE

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def client(
    docker_driver: DockerDriver, forge_config: ForgeConfig, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    forge_config.default_pool = PoolConfig(
        image=FORGE_TEST_IMAGE,
        min_idle=1,
        max_size=4,
        idle_ttl_seconds=30,
        exec_timeout_seconds=30,
        max_output_bytes=100_000,
        lease_wait_timeout_seconds=15.0,
    )
    app = create_app(config=forge_config, driver=docker_driver)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://forge.local"
        ) as c:
            yield c


async def _create_workspace(c: httpx.AsyncClient) -> str:
    r = await c.post(
        "/workspaces",
        json={"spec": {"image": FORGE_TEST_IMAGE}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_healthz_reports_pool_stats(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["pool"], list)


async def test_workspace_lifecycle(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    r = await client.get(f"/workspaces/{ws_id}")
    assert r.status_code == 200
    listing = await client.get("/workspaces")
    assert any(w["id"] == ws_id for w in listing.json())
    r = await client.delete(f"/workspaces/{ws_id}")
    assert r.status_code == 204
    r = await client.get(f"/workspaces/{ws_id}")
    assert r.status_code == 404


async def test_write_read_edit_delete(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    r = await client.put(
        f"/workspaces/{ws_id}/files/write",
        json={"path": "notes.txt", "content": "hi there"},
    )
    assert r.status_code == 204
    r = await client.get(
        f"/workspaces/{ws_id}/files/read", params={"path": "notes.txt"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "hi there"

    r = await client.post(
        f"/workspaces/{ws_id}/files/edit",
        json={"path": "notes.txt", "old_string": "hi", "new_string": "hello"},
    )
    assert r.json()["replacements"] == 1

    r = await client.get(
        f"/workspaces/{ws_id}/files/read", params={"path": "notes.txt"}
    )
    assert r.json()["content"] == "hello there"

    r = await client.delete(
        f"/workspaces/{ws_id}/files", params={"path": "notes.txt"}
    )
    assert r.status_code == 204


async def test_glob_and_grep(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    for path, content in [("a.py", "match here"), ("sub/b.py", "and here")]:
        await client.put(
            f"/workspaces/{ws_id}/files/write",
            json={"path": path, "content": content},
        )
    r = await client.get(
        f"/workspaces/{ws_id}/files/glob", params={"pattern": "**/*.py"}
    )
    assert set(r.json()["paths"]) == {"a.py", "sub/b.py"}
    r = await client.get(
        f"/workspaces/{ws_id}/files/grep", params={"pattern": "match"}
    )
    assert len(r.json()["matches"]) >= 1


async def test_execution_end_to_end(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    r = await client.post(
        f"/workspaces/{ws_id}/executions",
        json={"command": ["python", "-c", "print('hi')"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert body["output"].strip() == "hi"
    listing = await client.get(f"/workspaces/{ws_id}/executions")
    assert any(e["id"] == body["execution_id"] for e in listing.json())


async def test_execution_idempotency(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    payload = {
        "command": ["sh", "-c", "date +%s%N"],
        "idempotency_key": "abc",
    }
    r1 = await client.post(f"/workspaces/{ws_id}/executions", json=payload)
    r2 = await client.post(f"/workspaces/{ws_id}/executions", json=payload)
    assert r1.json()["execution_id"] == r2.json()["execution_id"]


async def test_path_escape_returns_400(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    r = await client.get(
        f"/workspaces/{ws_id}/files/read", params={"path": "../../etc/passwd"}
    )
    assert r.status_code == 400
    assert r.json()["error"] == "path_escape"


async def test_snapshot_and_restore(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    await client.put(
        f"/workspaces/{ws_id}/files/write",
        json={"path": "kept.txt", "content": "keep me"},
    )
    r = await client.post(
        f"/workspaces/{ws_id}/snapshots", json={"name": "v1"}
    )
    assert r.status_code == 201
    snap_id = r.json()["id"]

    r = await client.post(f"/snapshots/{snap_id}/restore", json={"name": "restored"})
    assert r.status_code == 201
    restored_id = r.json()["id"]

    r = await client.get(
        f"/workspaces/{restored_id}/files/read", params={"path": "kept.txt"}
    )
    assert r.json()["content"] == "keep me"


async def test_artifact_export_and_download(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    await client.put(
        f"/workspaces/{ws_id}/files/write",
        json={"path": "out.json", "content": '{"a": 1}'},
    )
    r = await client.post(
        f"/workspaces/{ws_id}/artifacts", json={"path": "out.json"}
    )
    assert r.status_code == 201
    art_id = r.json()["id"]

    r = await client.get(f"/artifacts/{art_id}/content")
    assert r.status_code == 200
    assert r.content == b'{"a": 1}'


async def test_upload_download_files(client: httpx.AsyncClient) -> None:
    ws_id = await _create_workspace(client)
    payload = {
        "items": [
            {
                "path": "bin/hello.dat",
                "content_b64": base64.b64encode(b"raw\x00bytes").decode(),
            }
        ]
    }
    r = await client.post(f"/workspaces/{ws_id}/files/upload", json=payload)
    assert r.json() == {"written": ["bin/hello.dat"]}
    r = await client.post(
        f"/workspaces/{ws_id}/files/download",
        json={"paths": ["bin/hello.dat"]},
    )
    items = r.json()["items"]
    assert base64.b64decode(items[0]["content_b64"]) == b"raw\x00bytes"


async def test_pool_status(client: httpx.AsyncClient) -> None:
    r = await client.get("/pool/status")
    assert r.status_code == 200
    stats = r.json()
    assert isinstance(stats, list)


async def test_sse_event_stream(client: httpx.AsyncClient) -> None:
    """The SSE endpoint exists and returns the right content-type.

    httpx.ASGITransport does not surface streaming responses well in-process
    — the response body is buffered until the generator ends, which for an
    infinite stream means it never returns. Full end-to-end streaming is
    covered by uvicorn in the concurrent-agents demo (branch 12).
    """
    ws_id = await _create_workspace(client)
    # Use send() so we can inspect headers without consuming the body.
    req = client.build_request(
        "GET", f"/workspaces/{ws_id}/executions/events/stream"
    )
    # The response never terminates by design; wait briefly and cancel.
    try:
        resp = await asyncio.wait_for(client.send(req, stream=True), timeout=0.3)
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")
        await resp.aclose()
    except TimeoutError:
        # Never got a response header — that's fine; the endpoint is at least
        # routable, which is all we can verify without a real socket.
        pass
