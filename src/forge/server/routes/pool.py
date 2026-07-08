"""Pool admin routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from forge.models import PoolStats
from forge.server.deps import ForgeState, get_state

router = APIRouter(prefix="/pool", tags=["pool"])


@router.get("/status", response_model=list[PoolStats])
async def pool_status(
    image: str | None = Query(default=None),
    state: ForgeState = Depends(get_state),
) -> list[PoolStats]:
    return state.pool.stats(image)
