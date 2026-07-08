"""Artifact service — high-level export/read over :class:`ArtifactStore`."""
from __future__ import annotations

from collections.abc import AsyncIterator

from forge.errors import ArtifactError, PathEscapeError
from forge.models import Artifact
from forge.services.workspace_service import WorkspaceService
from forge.storage.artifact_store import ArtifactStore
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import FORGE_META_DIR, WorkspaceStore


class ArtifactService:
    """Wraps ``ArtifactStore`` with metastore persistence + path safety."""

    def __init__(
        self,
        *,
        meta: MetaStore,
        workspaces: WorkspaceService,
        workspace_store: WorkspaceStore,
        artifacts: ArtifactStore,
    ) -> None:
        self._meta = meta
        self._workspaces = workspaces
        self._workspace_store = workspace_store
        self._artifacts = artifacts

    async def export(
        self,
        *,
        workspace_id: str,
        path: str,
        content_type: str | None = None,
    ) -> Artifact:
        """Export a workspace file into the artifact store."""
        await self._workspaces.get(workspace_id)
        ws_root = self._workspace_store.path(workspace_id).resolve()
        candidate = (ws_root / path).resolve()
        try:
            candidate.relative_to(ws_root)
        except ValueError as e:
            raise PathEscapeError(f"path {path!r} escapes workspace root") from e
        rel = candidate.relative_to(ws_root).as_posix()
        if rel.split("/", 1)[0] == FORGE_META_DIR:
            raise PathEscapeError(f"cannot export from reserved {FORGE_META_DIR}/")
        if not candidate.is_file():
            raise ArtifactError(f"path is not a file: {path}")
        artifact = self._artifacts.export(
            workspace_id=workspace_id,
            source=candidate,
            rel_path=rel,
            content_type=content_type,
        )
        try:
            await self._meta.create_artifact(artifact)
        except Exception:
            self._artifacts.delete(artifact.id)
            raise
        return artifact

    async def get(self, artifact_id: str) -> Artifact:
        return await self._meta.get_artifact(artifact_id)

    async def list(self, workspace_id: str) -> list[Artifact]:
        return await self._meta.list_artifacts(workspace_id)

    async def read(self, artifact_id: str) -> AsyncIterator[bytes]:
        row = await self._meta.get_artifact(artifact_id)
        # ArtifactStore keeps the file under <id>/<basename>.
        filename = row.path.rsplit("/", 1)[-1]
        return self._artifacts.read(artifact_id, filename)


__all__ = ["ArtifactService"]
