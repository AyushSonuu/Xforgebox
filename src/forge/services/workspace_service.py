"""High-level workspace CRUD service.

Composes :class:`~forge.storage.meta_store.MetaStore` and
:class:`~forge.storage.workspace_store.WorkspaceStore` so callers get one
handle for every workspace-level operation.

The MVP does not attempt any distributed coordination: metastore writes and
on-disk mkdir happen in that order. If the on-disk step fails, we mark the
row ``failed`` and leave the caller to clean up (or, more realistically, to
retry with a fresh UUID).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from forge.errors import WorkspaceError
from forge.models import Workspace, WorkspaceSpec
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import WorkspaceStore


class WorkspaceService:
    """One-stop entry point for workspace lifecycle operations."""

    def __init__(self, meta: MetaStore, workspaces: WorkspaceStore) -> None:
        self._meta = meta
        self._workspaces = workspaces

    async def create(
        self,
        spec: WorkspaceSpec,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Workspace:
        """Create a workspace: mkdir + metastore row, return the persisted model."""
        ws_id = f"ws_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        ws = Workspace(
            id=ws_id,
            spec=spec,
            status="ready",
            name=name,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        # Filesystem first — if this fails we never persist a phantom row.
        self._workspaces.create(ws_id)
        try:
            await self._meta.create_workspace(ws)
        except Exception:
            # Roll back the directory to keep the two stores in sync.
            self._workspaces.delete(ws_id)
            raise
        return ws

    async def get(self, workspace_id: str) -> Workspace:
        return await self._meta.get_workspace(workspace_id)

    async def list(
        self, *, status: str | None = None, limit: int | None = None
    ) -> list[Workspace]:
        return await self._meta.list_workspaces(status=status, limit=limit)

    async def delete(self, workspace_id: str) -> None:
        """Best-effort delete.

        Marks the row ``deleting`` first (for observability), removes the
        on-disk tree, then removes the row. Any error mid-flow leaves the
        row in ``deleting`` — a future reconciler task can retry.
        """
        # Confirm the row exists before we touch disk. get_workspace raises
        # NotFoundError which propagates to the caller.
        await self._meta.get_workspace(workspace_id)
        await self._meta.update_workspace_status(
            workspace_id, "deleting", datetime.now(UTC)
        )
        try:
            self._workspaces.delete(workspace_id)
        except OSError as e:
            raise WorkspaceError(f"failed to delete workspace tree: {e}") from e
        await self._meta.delete_workspace(workspace_id)


__all__ = ["WorkspaceService"]
