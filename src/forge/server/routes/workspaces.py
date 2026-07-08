"""Workspace lifecycle routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from forge.models import (
    CreateWorkspaceRequest,
    Workspace,
)
from forge.server.deps import ForgeState, get_state

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=Workspace, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    req: CreateWorkspaceRequest, state: ForgeState = Depends(get_state)
) -> Workspace:
    return await state.workspaces.create(
        spec=req.spec,
        name=req.name,
        metadata=req.metadata,
    )


@router.get("", response_model=list[Workspace])
async def list_workspaces(state: ForgeState = Depends(get_state)) -> list[Workspace]:
    return await state.workspaces.list()


@router.get("/{workspace_id}", response_model=Workspace)
async def get_workspace(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> Workspace:
    return await state.workspaces.get(workspace_id)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> None:
    await state.workspaces.delete(workspace_id)
