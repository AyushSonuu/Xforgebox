"""In-process transport — instantiates the full service stack, no HTTP.

Useful for embedded contexts (single-process AI apps) and for tests that want
to bypass the network. Interface-compatible with :class:`HTTPClient`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from forge.config import ForgeConfig, make_config
from forge.drivers.docker_driver import DockerDriver
from forge.events.bus import EventBus
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
from forge.pool.container_pool import ContainerPool
from forge.services.artifact_service import ArtifactService
from forge.services.execution_service import ExecutionService
from forge.services.files_service import FilesService, UploadItem
from forge.services.snapshot_service import SnapshotService
from forge.services.workspace_service import WorkspaceService
from forge.storage.artifact_store import ArtifactStore
from forge.storage.meta_store import MetaStore
from forge.storage.snapshot_store import SnapshotStore
from forge.storage.workspace_store import WorkspaceStore


class LocalClient:
    """In-process transport.

    Call ``start()`` (or use it inside ``async with Forge.local() as forge``)
    before making requests.
    """

    def __init__(
        self,
        *,
        config: ForgeConfig | None = None,
        driver: DockerDriver | None = None,
    ) -> None:
        self._config = config or make_config()
        self._config.ensure_layout()
        self._owns_driver = driver is None
        self._driver = driver or DockerDriver()

        # Storage.
        self._meta = MetaStore(self._config.meta_db_path)
        self._workspace_store = WorkspaceStore(self._config.workspaces_root)
        self._snap_store = SnapshotStore(self._config.snapshots_root)
        self._art_store = ArtifactStore(self._config.artifacts_root)

        # Services + pool are wired after ``start()`` because MetaStore needs
        # an awaited connect().
        self._workspaces: WorkspaceService | None = None
        self._files: FilesService | None = None
        self._snapshots: SnapshotService | None = None
        self._artifacts: ArtifactService | None = None
        self._executions: ExecutionService | None = None
        self._pool: ContainerPool | None = None
        self._events = EventBus()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self._meta.connect()
        self._workspaces = WorkspaceService(self._meta, self._workspace_store)
        self._files = FilesService(self._workspace_store)
        self._snapshots = SnapshotService(
            meta=self._meta,
            workspaces=self._workspaces,
            workspace_store=self._workspace_store,
            snapshots=self._snap_store,
        )
        self._artifacts = ArtifactService(
            meta=self._meta,
            workspaces=self._workspaces,
            workspace_store=self._workspace_store,
            artifacts=self._art_store,
        )
        self._pool = ContainerPool(driver=self._driver, config=self._config)
        await self._pool.start()
        self._executions = ExecutionService(
            meta=self._meta,
            workspaces=self._workspaces,
            workspace_store=self._workspace_store,
            pool=self._pool,
            events=self._events,
        )
        self._started = True

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.shutdown()
        if self._owns_driver:
            await self._driver.close()
        await self._meta.close()

    # ------------------------------------------------------------------
    # Delegations — mirror HTTPClient.
    # ------------------------------------------------------------------

    def _ws_svc(self) -> WorkspaceService:
        assert self._workspaces is not None, "start() before use"
        return self._workspaces

    def _files_svc(self) -> FilesService:
        assert self._files is not None
        return self._files

    def _exec_svc(self) -> ExecutionService:
        assert self._executions is not None
        return self._executions

    def _snap_svc(self) -> SnapshotService:
        assert self._snapshots is not None
        return self._snapshots

    def _art_svc(self) -> ArtifactService:
        assert self._artifacts is not None
        return self._artifacts

    def _pool_ref(self) -> ContainerPool:
        assert self._pool is not None
        return self._pool

    async def create_workspace(
        self,
        spec: WorkspaceSpec,
        *,
        name: str | None,
        metadata: dict[str, str],
    ) -> Workspace:
        return await self._ws_svc().create(spec, name=name, metadata=metadata)

    async def get_workspace(self, workspace_id: str) -> Workspace:
        return await self._ws_svc().get(workspace_id)

    async def list_workspaces(self) -> list[Workspace]:
        return await self._ws_svc().list()

    async def delete_workspace(self, workspace_id: str) -> None:
        await self._ws_svc().delete(workspace_id)

    async def ls(self, workspace_id: str, path: str = ".") -> list[FileListEntry]:
        return self._files_svc().ls(workspace_id, path)

    async def read(
        self,
        workspace_id: str,
        path: str,
        *,
        offset: int,
        limit: int | None,
    ) -> FileReadResult:
        content, total, truncated = self._files_svc().read(
            workspace_id, path, offset=offset,
            limit=limit if limit is not None else 2000,
        )
        return FileReadResult(
            path=path, content=content, offset=offset, limit=limit,
            total_lines=total, truncated=truncated,
        )

    async def write(self, workspace_id: str, path: str, content: str) -> None:
        self._files_svc().write(workspace_id, path, content)

    async def edit(
        self,
        workspace_id: str,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool,
    ) -> int:
        return self._files_svc().edit(
            workspace_id, path, old_string, new_string, replace_all=replace_all
        )

    async def glob(self, workspace_id: str, pattern: str) -> list[str]:
        return self._files_svc().glob(workspace_id, pattern)

    async def grep(
        self, workspace_id: str, pattern: str, *, path_glob: str | None
    ) -> list[dict[str, Any]]:
        hits = self._files_svc().grep(workspace_id, pattern, path_glob=path_glob)
        return [{"path": h.path, "line": h.line, "text": h.text} for h in hits]

    async def delete_file(self, workspace_id: str, path: str) -> None:
        self._files_svc().delete(workspace_id, path)

    async def upload_files(
        self, workspace_id: str, items: list[dict[str, str]]
    ) -> list[str]:
        return self._files_svc().upload_files(
            workspace_id,
            [UploadItem(path=i["path"], content_b64=i["content_b64"]) for i in items],
        )

    async def download_files(
        self, workspace_id: str, paths: list[str]
    ) -> list[dict[str, str]]:
        out = self._files_svc().download_files(workspace_id, paths)
        return [{"path": x.path, "content_b64": x.content_b64} for x in out]

    async def exec(
        self, workspace_id: str, req: ExecRequest
    ) -> ExecutionResult:
        return await self._exec_svc().exec(workspace_id, req)

    async def list_executions(self, workspace_id: str) -> list[Execution]:
        return await self._exec_svc().list(workspace_id)

    async def get_execution(
        self, workspace_id: str, execution_id: str
    ) -> Execution:
        return await self._exec_svc().get(execution_id)

    async def create_snapshot(
        self, workspace_id: str, *, name: str | None
    ) -> Snapshot:
        return await self._snap_svc().create(workspace_id=workspace_id, name=name)

    async def list_snapshots(self, workspace_id: str) -> list[Snapshot]:
        return await self._snap_svc().list(workspace_id)

    async def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        name: str | None,
        metadata: dict[str, str],
    ) -> Workspace:
        return await self._snap_svc().restore(
            snapshot_id=snapshot_id, name=name, metadata=metadata
        )

    async def export_artifact(
        self,
        workspace_id: str,
        path: str,
        *,
        content_type: str | None,
    ) -> Artifact:
        return await self._art_svc().export(
            workspace_id=workspace_id, path=path, content_type=content_type
        )

    async def read_artifact(self, artifact_id: str) -> AsyncIterator[bytes]:
        return await self._art_svc().read(artifact_id)

    async def pool_status(self, image: str | None = None) -> list[PoolStats]:
        return self._pool_ref().stats(image)

    async def subscribe(
        self, workspace_id: str
    ) -> AsyncIterator[ForgeEvent]:
        return self._events.subscribe(workspace_id=workspace_id)


__all__ = ["LocalClient"]
