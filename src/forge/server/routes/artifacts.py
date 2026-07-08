"""Artifact routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from forge.models import Artifact, ArtifactExportRequest
from forge.server.deps import ForgeState, get_state

router = APIRouter(prefix="", tags=["artifacts"])


@router.post(
    "/workspaces/{workspace_id}/artifacts",
    response_model=Artifact,
    status_code=status.HTTP_201_CREATED,
)
async def export_artifact(
    workspace_id: str,
    req: ArtifactExportRequest,
    state: ForgeState = Depends(get_state),
) -> Artifact:
    return await state.artifacts.export(
        workspace_id=workspace_id, path=req.path, content_type=req.content_type
    )


@router.get("/workspaces/{workspace_id}/artifacts", response_model=list[Artifact])
async def list_artifacts(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> list[Artifact]:
    return await state.artifacts.list(workspace_id)


@router.get("/artifacts/{artifact_id}", response_model=Artifact)
async def get_artifact(
    artifact_id: str, state: ForgeState = Depends(get_state)
) -> Artifact:
    return await state.artifacts.get(artifact_id)


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact(
    artifact_id: str, state: ForgeState = Depends(get_state)
) -> StreamingResponse:
    art = await state.artifacts.get(artifact_id)
    stream = await state.artifacts.read(artifact_id)
    return StreamingResponse(
        stream,
        media_type=art.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{art.path.split("/")[-1]}"'},
    )
