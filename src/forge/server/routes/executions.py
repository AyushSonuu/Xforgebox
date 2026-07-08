"""Execution routes — synchronous exec + SSE log stream."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from forge.models import (
    ExecRequest,
    Execution,
    ExecutionResult,
)
from forge.server.deps import ForgeState, get_state

router = APIRouter(prefix="/workspaces/{workspace_id}/executions", tags=["executions"])


@router.post("", response_model=ExecutionResult)
async def create_execution(
    workspace_id: str,
    req: ExecRequest,
    state: ForgeState = Depends(get_state),
) -> ExecutionResult:
    return await state.executions.exec(workspace_id, req)


@router.get("", response_model=list[Execution])
async def list_executions(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> list[Execution]:
    return await state.executions.list(workspace_id)


@router.get("/{execution_id}", response_model=Execution)
async def get_execution(
    workspace_id: str,
    execution_id: str,
    state: ForgeState = Depends(get_state),
) -> Execution:
    return await state.executions.get(execution_id)


@router.get("/events/stream")
async def stream_events(
    workspace_id: str, state: ForgeState = Depends(get_state)
) -> StreamingResponse:
    """SSE stream of lifecycle events for one workspace's executions."""

    async def _sse() -> AsyncIterator[bytes]:
        async for event in state.events.subscribe(workspace_id=workspace_id):
            payload = json.dumps(event.model_dump(mode="json"), default=str)
            yield f"event: {event.kind}\ndata: {payload}\n\n".encode()

    return StreamingResponse(_sse(), media_type="text/event-stream")
