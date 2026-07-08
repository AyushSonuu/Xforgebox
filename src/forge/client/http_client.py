"""HTTP transport for the Forge SDK."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from forge.errors import (
    ArtifactError,
    ConflictError,
    ForgeError,
    NotFoundError,
    PathEscapeError,
    PoolExhaustedError,
    SnapshotError,
    WorkspaceError,
)
from forge.models import (
    Artifact,
    ExecRequest,
    Execution,
    ExecutionResult,
    FileListEntry,
    FileReadResult,
    ForgeEvent,
    PoolStats,
    Snapshot,
    Workspace,
    WorkspaceSpec,
)

_ERROR_MAP: dict[str, type[ForgeError]] = {
    "not_found": NotFoundError,
    "path_escape": PathEscapeError,
    "workspace_error": WorkspaceError,
    "conflict": ConflictError,
    "pool_exhausted": PoolExhaustedError,
    "snapshot_error": SnapshotError,
    "artifact_error": ArtifactError,
}


class HTTPClient:
    """Thin :class:`httpx.AsyncClient` wrapper implementing ``ForgeTransport``."""

    def __init__(self, url: str, *, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    async def create_workspace(
        self,
        spec: WorkspaceSpec,
        *,
        name: str | None,
        metadata: dict[str, str],
    ) -> Workspace:
        r = await self._client.post(
            "/workspaces",
            json={
                "spec": spec.model_dump(mode="json"),
                "name": name,
                "metadata": metadata,
            },
        )
        return Workspace.model_validate(_ok(r))

    async def get_workspace(self, workspace_id: str) -> Workspace:
        r = await self._client.get(f"/workspaces/{workspace_id}")
        return Workspace.model_validate(_ok(r))

    async def list_workspaces(self) -> list[Workspace]:
        r = await self._client.get("/workspaces")
        return [Workspace.model_validate(x) for x in _ok(r)]

    async def delete_workspace(self, workspace_id: str) -> None:
        r = await self._client.delete(f"/workspaces/{workspace_id}")
        _ok(r, expect_status=(204,))

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def ls(self, workspace_id: str, path: str = ".") -> list[FileListEntry]:
        r = await self._client.get(
            f"/workspaces/{workspace_id}/files", params={"path": path}
        )
        return [FileListEntry.model_validate(x) for x in _ok(r)]

    async def read(
        self,
        workspace_id: str,
        path: str,
        *,
        offset: int,
        limit: int | None,
    ) -> FileReadResult:
        params: dict[str, Any] = {"path": path, "offset": offset}
        if limit is not None:
            params["limit"] = limit
        r = await self._client.get(
            f"/workspaces/{workspace_id}/files/read", params=params
        )
        return FileReadResult.model_validate(_ok(r))

    async def write(self, workspace_id: str, path: str, content: str) -> None:
        r = await self._client.put(
            f"/workspaces/{workspace_id}/files/write",
            json={"path": path, "content": content},
        )
        _ok(r, expect_status=(204,))

    async def edit(
        self,
        workspace_id: str,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool,
    ) -> int:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/files/edit",
            json={
                "path": path,
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": replace_all,
            },
        )
        return int(_ok(r)["replacements"])

    async def glob(self, workspace_id: str, pattern: str) -> list[str]:
        r = await self._client.get(
            f"/workspaces/{workspace_id}/files/glob", params={"pattern": pattern}
        )
        return list(_ok(r)["paths"])

    async def grep(
        self, workspace_id: str, pattern: str, *, path_glob: str | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pattern": pattern}
        if path_glob is not None:
            params["path_glob"] = path_glob
        r = await self._client.get(
            f"/workspaces/{workspace_id}/files/grep", params=params
        )
        return list(_ok(r)["matches"])

    async def delete_file(self, workspace_id: str, path: str) -> None:
        r = await self._client.delete(
            f"/workspaces/{workspace_id}/files", params={"path": path}
        )
        _ok(r, expect_status=(204,))

    async def upload_files(
        self, workspace_id: str, items: list[dict[str, str]]
    ) -> list[str]:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/files/upload",
            json={"items": items},
        )
        return list(_ok(r)["written"])

    async def download_files(
        self, workspace_id: str, paths: list[str]
    ) -> list[dict[str, str]]:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/files/download",
            json={"paths": paths},
        )
        return list(_ok(r)["items"])

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------

    async def exec(self, workspace_id: str, req: ExecRequest) -> ExecutionResult:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/executions",
            json=req.model_dump(mode="json"),
        )
        return ExecutionResult.model_validate(_ok(r))

    async def list_executions(self, workspace_id: str) -> list[Execution]:
        r = await self._client.get(f"/workspaces/{workspace_id}/executions")
        return [Execution.model_validate(x) for x in _ok(r)]

    async def get_execution(
        self, workspace_id: str, execution_id: str
    ) -> Execution:
        r = await self._client.get(
            f"/workspaces/{workspace_id}/executions/{execution_id}"
        )
        return Execution.model_validate(_ok(r))

    # ------------------------------------------------------------------
    # Snapshots + artifacts + pool
    # ------------------------------------------------------------------

    async def create_snapshot(
        self, workspace_id: str, *, name: str | None
    ) -> Snapshot:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/snapshots", json={"name": name}
        )
        return Snapshot.model_validate(_ok(r))

    async def list_snapshots(self, workspace_id: str) -> list[Snapshot]:
        r = await self._client.get(f"/workspaces/{workspace_id}/snapshots")
        return [Snapshot.model_validate(x) for x in _ok(r)]

    async def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        name: str | None,
        metadata: dict[str, str],
    ) -> Workspace:
        r = await self._client.post(
            f"/snapshots/{snapshot_id}/restore",
            json={"name": name, "metadata": metadata},
        )
        return Workspace.model_validate(_ok(r))

    async def export_artifact(
        self,
        workspace_id: str,
        path: str,
        *,
        content_type: str | None,
    ) -> Artifact:
        r = await self._client.post(
            f"/workspaces/{workspace_id}/artifacts",
            json={"path": path, "content_type": content_type},
        )
        return Artifact.model_validate(_ok(r))

    async def read_artifact(self, artifact_id: str) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            async with self._client.stream(
                "GET", f"/artifacts/{artifact_id}/content"
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    _raise_error(resp.status_code, body)
                async for chunk in resp.aiter_bytes():
                    yield chunk

        return _stream()

    async def pool_status(self, image: str | None = None) -> list[PoolStats]:
        params: dict[str, str] = {}
        if image is not None:
            params["image"] = image
        r = await self._client.get("/pool/status", params=params)
        return [PoolStats.model_validate(x) for x in _ok(r)]

    async def subscribe(
        self, workspace_id: str
    ) -> AsyncIterator[ForgeEvent]:
        """Consume SSE events from the daemon. Yields until the caller stops."""

        async def _stream() -> AsyncIterator[ForgeEvent]:
            async with self._client.stream(
                "GET", f"/workspaces/{workspace_id}/executions/events/stream"
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    _raise_error(resp.status_code, body)
                buf = ""
                async for chunk in resp.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        for line in raw.splitlines():
                            if line.startswith("data: "):
                                payload = json.loads(line[len("data: ") :])
                                yield ForgeEvent.model_validate(payload)

        return _stream()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ok(resp: httpx.Response, *, expect_status: tuple[int, ...] = (200, 201)) -> Any:
    if resp.status_code in expect_status:
        if resp.status_code == 204:
            return None
        return resp.json()
    _raise_error(resp.status_code, resp.content)


def _raise_error(status: int, body: bytes) -> None:
    try:
        payload = json.loads(body)
    except Exception:
        raise ForgeError(f"HTTP {status}: {body!r}") from None
    kind = payload.get("error", "")
    message = payload.get("message", str(payload))
    exc_cls = _ERROR_MAP.get(kind, ForgeError)
    raise exc_cls(message)


__all__ = ["HTTPClient"]
