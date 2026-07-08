"""Shared server state + FastAPI dependency helpers."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from forge.config import ForgeConfig
from forge.drivers.docker_driver import DockerDriver
from forge.events.bus import EventBus
from forge.pool.container_pool import ContainerPool
from forge.services.artifact_service import ArtifactService
from forge.services.execution_service import ExecutionService
from forge.services.files_service import FilesService
from forge.services.snapshot_service import SnapshotService
from forge.services.workspace_service import WorkspaceService
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import WorkspaceStore


@dataclass(slots=True)
class ForgeState:
    """Every service the routes need — attached to ``app.state.forge``."""

    config: ForgeConfig
    meta: MetaStore
    workspace_store: WorkspaceStore
    workspaces: WorkspaceService
    files: FilesService
    driver: DockerDriver
    pool: ContainerPool
    events: EventBus
    executions: ExecutionService
    snapshots: SnapshotService
    artifacts: ArtifactService


def get_state(request: Request) -> ForgeState:
    return request.app.state.forge  # type: ignore[no-any-return]
