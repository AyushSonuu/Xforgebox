"""Snapshot service — orchestrates snapshot creation + restore.

Restore creates a brand-new workspace (fresh UUID) whose spec is inherited
from the source workspace's spec. This keeps snapshots orthogonal to running
executions and makes it safe to restore while the original workspace still
has work in flight.
"""
from __future__ import annotations

import logging
from pathlib import Path

from forge.errors import SnapshotError
from forge.models import Snapshot, Workspace, WorkspaceSpec
from forge.services.workspace_service import WorkspaceService
from forge.storage.meta_store import MetaStore
from forge.storage.snapshot_store import SnapshotStore, ensure_dir_empty
from forge.storage.workspace_store import WorkspaceStore

log = logging.getLogger(__name__)


class SnapshotService:
    """High-level snapshot / restore API."""

    def __init__(
        self,
        *,
        meta: MetaStore,
        workspaces: WorkspaceService,
        workspace_store: WorkspaceStore,
        snapshots: SnapshotStore,
    ) -> None:
        self._meta = meta
        self._workspaces = workspaces
        self._workspace_store = workspace_store
        self._snapshots = snapshots

    async def create(
        self,
        *,
        workspace_id: str,
        name: str | None = None,
    ) -> Snapshot:
        ws = await self._workspaces.get(workspace_id)
        path = self._workspace_store.path(workspace_id)
        snap = self._snapshots.create(
            workspace_id=ws.id,
            workspace_path=path,
            name=name,
        )
        try:
            await self._meta.create_snapshot(snap)
        except Exception:
            # Roll back the archive so we don't leak orphan files.
            self._snapshots.delete(snap.id)
            raise
        log.info("snapshot.created ws=%s snap=%s bytes=%s", ws.id, snap.id, snap.size_bytes)
        return snap

    async def get(self, snapshot_id: str) -> Snapshot:
        return await self._meta.get_snapshot(snapshot_id)

    async def list(self, workspace_id: str) -> list[Snapshot]:
        return await self._meta.list_snapshots(workspace_id)

    async def restore(
        self,
        *,
        snapshot_id: str,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Workspace:
        """Restore a snapshot into a brand-new workspace and return it."""
        snap = await self._meta.get_snapshot(snapshot_id)
        source_ws = await self._workspaces.get(snap.workspace_id)
        spec = _copy_spec(source_ws.spec)

        new_ws = await self._workspaces.create(spec, name=name, metadata=metadata)

        destination = self._workspace_store.path(new_ws.id)
        # WorkspaceService.create left it with just .forge/exec/ inside; wipe
        # for a clean overlay.
        ensure_dir_empty(destination)

        try:
            self._snapshots.restore(snapshot_id, destination)
        except SnapshotError:
            # Clean the half-materialized workspace so callers can retry safely.
            await self._workspaces.delete(new_ws.id)
            raise

        # Re-create .forge/exec so subsequent executions can spill overflow logs
        # without having to check for the dir existing first.
        _ensure_forge_meta(destination)
        return new_ws


def _copy_spec(spec: WorkspaceSpec) -> WorkspaceSpec:
    return WorkspaceSpec(
        image=spec.image,
        runtime=spec.runtime,
        working_dir=spec.working_dir,
        env=dict(spec.env),
        resources=spec.resources,
    )


def _ensure_forge_meta(destination: Path) -> None:
    (destination / ".forge" / "exec").mkdir(parents=True, exist_ok=True)


__all__ = ["SnapshotService"]
