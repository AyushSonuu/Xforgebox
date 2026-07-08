"""Aggregated FastAPI routers."""
from __future__ import annotations

from forge.server.routes.artifacts import router as artifacts_router
from forge.server.routes.executions import router as executions_router
from forge.server.routes.files import router as files_router
from forge.server.routes.pool import router as pool_router
from forge.server.routes.snapshots import router as snapshots_router
from forge.server.routes.workspaces import router as workspaces_router

__all__ = [
    "artifacts_router",
    "executions_router",
    "files_router",
    "pool_router",
    "snapshots_router",
    "workspaces_router",
]
