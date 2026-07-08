"""FastAPI application factory + top-level daemon assembly.

Everything wires together here so the daemon entrypoint (:mod:`forge.daemon`)
and tests can share the exact same startup / shutdown code.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from forge import __version__
from forge.config import ForgeConfig, make_config
from forge.drivers.docker_driver import DockerDriver
from forge.errors import (
    ArtifactError,
    ConflictError,
    NotFoundError,
    PathEscapeError,
    PoolExhaustedError,
    SnapshotError,
    WorkspaceError,
)
from forge.events.bus import EventBus
from forge.pool.container_pool import ContainerPool
from forge.server.deps import ForgeState
from forge.server.routes import (
    artifacts_router,
    executions_router,
    files_router,
    pool_router,
    snapshots_router,
    workspaces_router,
)
from forge.services.artifact_service import ArtifactService
from forge.services.execution_service import ExecutionService
from forge.services.files_service import FilesService
from forge.services.snapshot_service import SnapshotService
from forge.services.workspace_service import WorkspaceService
from forge.storage.artifact_store import ArtifactStore
from forge.storage.meta_store import MetaStore
from forge.storage.snapshot_store import SnapshotStore
from forge.storage.workspace_store import WorkspaceStore

log = logging.getLogger(__name__)


def create_app(
    *,
    config: ForgeConfig | None = None,
    driver: DockerDriver | None = None,
) -> FastAPI:
    """Build a fully-wired :class:`FastAPI` app.

    Callers usually don't pass ``driver`` — the default constructs a
    :class:`DockerDriver` and closes it on shutdown. Tests pass a driver
    they own so lifetimes are explicit.
    """
    cfg = config or make_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.ensure_layout()
        # Storage layer.
        meta = MetaStore(cfg.meta_db_path)
        await meta.connect()
        ws_store = WorkspaceStore(cfg.workspaces_root)
        snap_store = SnapshotStore(cfg.snapshots_root)
        art_store = ArtifactStore(cfg.artifacts_root)

        # Services.
        workspaces_svc = WorkspaceService(meta, ws_store)
        files_svc = FilesService(ws_store)
        snap_svc = SnapshotService(
            meta=meta,
            workspaces=workspaces_svc,
            workspace_store=ws_store,
            snapshots=snap_store,
        )
        art_svc = ArtifactService(
            meta=meta,
            workspaces=workspaces_svc,
            workspace_store=ws_store,
            artifacts=art_store,
        )
        events = EventBus()

        # Runtime.
        owns_driver = driver is None
        drv = driver or DockerDriver()
        pool = ContainerPool(driver=drv, config=cfg)
        try:
            await pool.start()
        except Exception:
            log.exception("forge: pool failed to warm; continuing without warm envs")

        exec_svc = ExecutionService(
            meta=meta,
            workspaces=workspaces_svc,
            workspace_store=ws_store,
            pool=pool,
            events=events,
        )

        app.state.forge = ForgeState(
            config=cfg,
            meta=meta,
            workspace_store=ws_store,
            workspaces=workspaces_svc,
            files=files_svc,
            driver=drv,
            pool=pool,
            events=events,
            executions=exec_svc,
            snapshots=snap_svc,
            artifacts=art_svc,
        )
        log.info("forge: daemon ready (version=%s, root=%s)", __version__, cfg.data_root)
        try:
            yield
        finally:
            await pool.shutdown()
            if owns_driver:
                await drv.close()
            await meta.close()

    app = FastAPI(
        title="Forge",
        version=__version__,
        description="Resource-pooled workspace runtime for AI agents.",
        lifespan=lifespan,
    )

    # Exception -> HTTP mapping. Keep this tight; anything else bubbles as 500.
    @app.exception_handler(NotFoundError)
    async def _not_found(_r: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @app.exception_handler(PathEscapeError)
    async def _path_escape(_r: Request, exc: PathEscapeError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "path_escape", "message": str(exc)})

    @app.exception_handler(WorkspaceError)
    async def _ws_err(_r: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "workspace_error", "message": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_r: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc)})

    @app.exception_handler(PoolExhaustedError)
    async def _pool_exhausted(_r: Request, exc: PoolExhaustedError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "pool_exhausted", "message": str(exc)},
        )

    @app.exception_handler(SnapshotError)
    async def _snap_err(_r: Request, exc: SnapshotError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "snapshot_error", "message": str(exc)})

    @app.exception_handler(ArtifactError)
    async def _art_err(_r: Request, exc: ArtifactError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "artifact_error", "message": str(exc)})

    @app.get("/healthz")
    async def _healthz() -> dict[str, object]:
        state: ForgeState = app.state.forge
        stats = state.pool.stats()
        return {
            "status": "ok",
            "version": __version__,
            "pool": [s.model_dump() for s in stats],
        }

    app.include_router(workspaces_router)
    app.include_router(files_router)
    app.include_router(executions_router)
    app.include_router(snapshots_router)
    app.include_router(artifacts_router)
    app.include_router(pool_router)
    return app


__all__ = ["ForgeState", "create_app"]
