"""Snapshot routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from forge.models import (
    Snapshot,
    SnapshotCreateRequest,
    SnapshotRestoreRequest,
    Workspace,
)
from forge.server.deps import ForgeState, get_state

router = APIRouter(prefix="", tags=["snapshots"])


@router.post(
    "/workspaces/{workspace_id}/snapshots",
    response_model=Snapshot,
    status_code=status.HTTP_201_CREATED,
)
async def create_snapshot(
    workspace_id: str,
    req: SnapshotCreateRequest,
    state: ForgeState = Depends(get_state),
) -> Snapshot:
    return await state.snapshots.create(workspace_id=workspace_id, name=req.name)


@router.get("/workspaces/{workspace_id}/snapshots", response_model=list[Snapshot])
async def list_snapshots(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> list[Snapshot]:
    return await state.snapshots.list(workspace_id)


@router.get("/snapshots/{snapshot_id}", response_model=Snapshot)
async def get_snapshot(
    snapshot_id: str, state: ForgeState = Depends(get_state)
) -> Snapshot:
    return await state.snapshots.get(snapshot_id)


@router.post(
    "/snapshots/{snapshot_id}/restore",
    response_model=Workspace,
    status_code=status.HTTP_201_CREATED,
)
async def restore_snapshot(
    snapshot_id: str,
    req: SnapshotRestoreRequest,
    state: ForgeState = Depends(get_state),
) -> Workspace:
    return await state.snapshots.restore(
        snapshot_id=snapshot_id, name=req.name, metadata=req.metadata
    )
