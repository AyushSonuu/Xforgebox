"""User-facing Forge SDK.

Two transports share the same surface:

- :class:`HTTPClient` talks to a running ``forged`` over HTTP (default).
- :class:`LocalClient` instantiates services in-process for embedded use
  (no daemon, no port).

Callers touch ``Forge``, ``WorkspaceHandle``, and helper subresources — never
the transports directly.
"""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any, Protocol

from forge.models import (
    Artifact,
    CreateWorkspaceRequest,
    ExecRequest,
    Execution,
    ExecutionResult,
    FileEditRequest,
    FileListEntry,
    FileReadResult,
    FileWriteRequest,
    ForgeEvent,
    PoolStats,
    ResourceLimits,
    Snapshot,
    SnapshotCreateRequest,
    SnapshotRestoreRequest,
    Workspace,
    WorkspaceSpec,
)


class ForgeTransport(Protocol):
    """Duck-typed transport contract for HTTP + local clients."""

    async def create_workspace(self, spec: WorkspaceSpec, *, name: str | None,
                               metadata: dict[str, str]) -> Workspace: ...
    async def get_workspace(self, workspace_id: str) -> Workspace: ...
    async def list_workspaces(self) -> list[Workspace]: ...
    async def delete_workspace(self, workspace_id: str) -> None: ...

    async def ls(self, workspace_id: str, path: str = ".") -> list[FileListEntry]: ...
    async def read(self, workspace_id: str, path: str, *, offset: int,
                   limit: int | None) -> FileReadResult: ...
    async def write(self, workspace_id: str, path: str, content: str) -> None: ...
    async def edit(self, workspace_id: str, path: str, old_string: str,
                   new_string: str, *, replace_all: bool) -> int: ...
    async def glob(self, workspace_id: str, pattern: str) -> list[str]: ...
    async def grep(self, workspace_id: str, pattern: str,
                   *, path_glob: str | None) -> list[dict[str, Any]]: ...
    async def delete_file(self, workspace_id: str, path: str) -> None: ...
    async def upload_files(self, workspace_id: str,
                           items: list[dict[str, str]]) -> list[str]: ...
    async def download_files(self, workspace_id: str,
                             paths: list[str]) -> list[dict[str, str]]: ...

    async def exec(self, workspace_id: str, req: ExecRequest) -> ExecutionResult: ...
    async def list_executions(self, workspace_id: str) -> list[Execution]: ...
    async def get_execution(self, workspace_id: str, execution_id: str) -> Execution: ...

    async def create_snapshot(self, workspace_id: str, *, name: str | None) -> Snapshot: ...
    async def list_snapshots(self, workspace_id: str) -> list[Snapshot]: ...
    async def restore_snapshot(self, snapshot_id: str, *, name: str | None,
                               metadata: dict[str, str]) -> Workspace: ...

    async def export_artifact(self, workspace_id: str, path: str,
                              *, content_type: str | None) -> Artifact: ...
    async def read_artifact(self, artifact_id: str) -> AsyncIterator[bytes]: ...

    async def pool_status(self, image: str | None = None) -> list[PoolStats]: ...

    async def subscribe(
        self, workspace_id: str
    ) -> AsyncIterator[ForgeEvent]: ...  # pragma: no cover — advisory

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class _WorkspaceFiles:
    """Files subresource — one instance per :class:`WorkspaceHandle`."""

    def __init__(self, transport: ForgeTransport, workspace_id: str) -> None:
        self._t = transport
        self._id = workspace_id

    async def ls(self, path: str = ".") -> list[FileListEntry]:
        return await self._t.ls(self._id, path)

    async def read(self, path: str, *, offset: int = 0, limit: int | None = None) -> FileReadResult:
        return await self._t.read(self._id, path, offset=offset, limit=limit)

    async def write(self, path: str, content: str) -> None:
        await self._t.write(self._id, path, content)

    async def edit(self, path: str, old_string: str, new_string: str, *,
                   replace_all: bool = False) -> int:
        return await self._t.edit(self._id, path, old_string, new_string, replace_all=replace_all)

    async def glob(self, pattern: str) -> list[str]:
        return await self._t.glob(self._id, pattern)

    async def grep(self, pattern: str, *, path_glob: str | None = None) -> list[dict[str, Any]]:
        return await self._t.grep(self._id, pattern, path_glob=path_glob)

    async def delete(self, path: str) -> None:
        await self._t.delete_file(self._id, path)

    async def upload(self, path: str, content: bytes) -> None:
        await self._t.upload_files(self._id, [
            {"path": path, "content_b64": base64.b64encode(content).decode()},
        ])

    async def download(self, path: str) -> bytes:
        items = await self._t.download_files(self._id, [path])
        return base64.b64decode(items[0]["content_b64"])


class _WorkspaceExecutions:
    def __init__(self, transport: ForgeTransport, workspace_id: str) -> None:
        self._t = transport
        self._id = workspace_id

    async def run(
        self,
        command: list[str],
        *,
        shell: bool = False,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionResult:
        req = ExecRequest(
            command=command,
            shell=shell,
            env=env or {},
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            idempotency_key=idempotency_key,
        )
        return await self._t.exec(self._id, req)

    async def list(self) -> list[Execution]:
        return await self._t.list_executions(self._id)


class _WorkspaceSnapshots:
    def __init__(self, transport: ForgeTransport, workspace_id: str) -> None:
        self._t = transport
        self._id = workspace_id

    async def create(self, *, name: str | None = None) -> Snapshot:
        return await self._t.create_snapshot(self._id, name=name)

    async def list(self) -> list[Snapshot]:
        return await self._t.list_snapshots(self._id)


class _WorkspaceArtifacts:
    def __init__(self, transport: ForgeTransport, workspace_id: str) -> None:
        self._t = transport
        self._id = workspace_id

    async def export(self, path: str, *, content_type: str | None = None) -> Artifact:
        return await self._t.export_artifact(self._id, path, content_type=content_type)

    async def read(self, artifact_id: str) -> AsyncIterator[bytes]:
        return await self._t.read_artifact(artifact_id)


class WorkspaceHandle:
    """User-facing handle to one workspace. Chained access to files/exec/etc."""

    def __init__(self, transport: ForgeTransport, model: Workspace) -> None:
        self._t = transport
        self._model = model
        self.files = _WorkspaceFiles(transport, model.id)
        self.executions = _WorkspaceExecutions(transport, model.id)
        self.snapshots = _WorkspaceSnapshots(transport, model.id)
        self.artifacts = _WorkspaceArtifacts(transport, model.id)

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def spec(self) -> WorkspaceSpec:
        return self._model.spec

    @property
    def model(self) -> Workspace:
        return self._model

    async def exec(
        self,
        command: list[str],
        *,
        shell: bool = False,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionResult:
        """Shortcut for ``ws.executions.run(...)``."""
        return await self.executions.run(
            command,
            shell=shell,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            idempotency_key=idempotency_key,
        )

    async def delete(self) -> None:
        await self._t.delete_workspace(self._model.id)


class _Workspaces:
    def __init__(self, transport: ForgeTransport) -> None:
        self._t = transport

    async def create(
        self,
        *,
        image: str = "python:3.14-slim",
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        resources: ResourceLimits | None = None,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> WorkspaceHandle:
        spec = WorkspaceSpec(
            image=image,
            working_dir=working_dir,
            env=env or {},
            resources=resources or ResourceLimits(),
        )
        model = await self._t.create_workspace(spec, name=name, metadata=metadata or {})
        return WorkspaceHandle(self._t, model)

    async def get(self, workspace_id: str) -> WorkspaceHandle:
        model = await self._t.get_workspace(workspace_id)
        return WorkspaceHandle(self._t, model)

    async def list(self) -> list[Workspace]:
        return await self._t.list_workspaces()


class Forge:
    """Entry point.

    Usage::

        async with Forge("http://localhost:8787") as forge:
            ws = await forge.workspaces.create(image="python:3.14-slim")
            result = await ws.exec(["python", "-c", "print('hi')"])
            print(result.output)
    """

    def __init__(self, url: str = "http://127.0.0.1:8787") -> None:
        from forge.client.http_client import HTTPClient

        self._transport: ForgeTransport = HTTPClient(url)
        self.workspaces = _Workspaces(self._transport)

    @classmethod
    def local(cls, *, config: Any = None, driver: Any = None) -> Forge:
        """Build a Forge that runs services in-process (no daemon).

        Use for tests or embedded contexts. All heavy dependencies are the
        same as the daemon; only the transport differs.
        """
        instance = cls.__new__(cls)
        from forge.client.local_client import LocalClient

        instance._transport = LocalClient(config=config, driver=driver)
        instance.workspaces = _Workspaces(instance._transport)
        return instance

    async def pool_status(self, image: str | None = None) -> list[PoolStats]:
        return await self._transport.pool_status(image)

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> Forge:
        # LocalClient needs an explicit start(); HTTPClient is stateless.
        start = getattr(self._transport, "start", None)
        if start is not None:
            await start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()


__all__ = [
    "CreateWorkspaceRequest",
    "ExecRequest",
    "FileEditRequest",
    "FileWriteRequest",
    "Forge",
    "ForgeTransport",
    "SnapshotCreateRequest",
    "SnapshotRestoreRequest",
    "WorkspaceHandle",
]
