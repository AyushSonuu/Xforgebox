"""End-to-end execution orchestration.

Ties :class:`~forge.storage.meta_store.MetaStore`,
:class:`~forge.pool.container_pool.ContainerPool`, and the workspace
filesystem together. This is what agents ultimately hit for every
tool-call execution.

Design notes
------------

- Idempotency: if ``ExecRequest.idempotency_key`` is set and a terminal
  execution already exists for ``(workspace_id, idempotency_key)``, we
  short-circuit and return the cached :class:`ExecutionResult`.
- Oversized output: the driver reports ``truncated=True`` when the buffer
  fills; the service writes the full output to
  ``.forge/exec/<exec_id>.log`` inside the workspace (host filesystem, so
  agents can read it via the files service in a subsequent turn if they
  really want to).
- Timeouts: driver returns exit_code=124 (GNU-timeout convention). We map
  that to status="timed_out" so downstream consumers don't need special-case
  logic for "was that a real 124 or a timeout?".
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

from forge.events.bus import EventBus
from forge.models import (
    ExecRequest,
    Execution,
    ExecutionResult,
    ForgeEvent,
)
from forge.pool.container_pool import ContainerPool
from forge.services.workspace_service import WorkspaceService
from forge.storage.meta_store import MetaStore
from forge.storage.workspace_store import FORGE_META_DIR, WorkspaceStore

log = logging.getLogger(__name__)


class ExecutionService:
    """Runs commands against a workspace via the container pool."""

    def __init__(
        self,
        *,
        meta: MetaStore,
        workspaces: WorkspaceService,
        workspace_store: WorkspaceStore,
        pool: ContainerPool,
        events: EventBus,
    ) -> None:
        self._meta = meta
        self._workspaces = workspaces
        self._workspace_store = workspace_store
        self._pool = pool
        self._events = events

    async def exec(self, workspace_id: str, req: ExecRequest) -> ExecutionResult:
        """Run one command; return a structured result (never raises for command failures)."""
        ws = await self._workspaces.get(workspace_id)

        # Idempotency short-circuit — return the cached result if we've already
        # run this exact request.
        if req.idempotency_key:
            existing = await self._meta.get_execution_by_idempotency(
                workspace_id, req.idempotency_key
            )
            if existing is not None and existing.status in {
                "succeeded", "failed", "timed_out", "cancelled"
            }:
                log.info(
                    "execution: idempotency hit ws=%s key=%s ex=%s",
                    workspace_id, req.idempotency_key, existing.id,
                )
                return _result_from_terminal_execution(
                    existing, self._workspace_store, workspace_id
                )

        exec_id = f"ex_{uuid.uuid4().hex[:16]}"
        row = Execution(
            id=exec_id,
            workspace_id=workspace_id,
            environment_id=None,
            command=list(req.command),
            shell=req.shell,
            status="queued",
            idempotency_key=req.idempotency_key,
        )
        await self._meta.create_execution(row)
        await self._publish("execution.queued", exec_id, workspace_id)

        started_at = datetime.now(UTC)
        started_mono = time.monotonic()
        await self._meta.update_execution(
            exec_id, status="running", started_at=started_at.isoformat()
        )
        await self._publish("execution.started", exec_id, workspace_id)

        cmd = _prepare_command(req)

        try:
            async with self._pool.session(
                workspace_id=workspace_id, image=ws.spec.image
            ) as sess:
                driver_result = await sess.exec(
                    cmd,
                    env=req.env,
                    timeout_seconds=req.timeout_seconds,
                    max_output_bytes=req.max_output_bytes,
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - started_mono) * 1000)
            log.exception("execution: infra failure ws=%s ex=%s", workspace_id, exec_id)
            await self._meta.update_execution(
                exec_id,
                status="failed",
                exit_code=None,
                finished_at=datetime.now(UTC).isoformat(),
            )
            await self._publish(
                "execution.finished",
                exec_id,
                workspace_id,
                payload={"error": str(e)},
            )
            # Infra failures — this is the one case we surface a structured
            # error instead of a normal ExecutionResult.
            raise

        finished_at = datetime.now(UTC)
        duration_ms = int((time.monotonic() - started_mono) * 1000)

        # If the driver truncated, spill the entire (buffered) chunk we DID
        # capture to a per-exec log inside the workspace. The driver only
        # keeps ``max_output_bytes`` — but at least we make what we have easy
        # to find later, and set output_path for consumers.
        output_path: str | None = None
        if driver_result.truncated:
            output_path = self._spill_overflow(workspace_id, exec_id, driver_result.output)

        status = _classify_status(driver_result.exit_code)

        await self._meta.update_execution(
            exec_id,
            status=status,
            exit_code=driver_result.exit_code,
            truncated=driver_result.truncated,
            output_path=output_path,
            finished_at=finished_at.isoformat(),
        )
        await self._publish(
            "execution.finished",
            exec_id,
            workspace_id,
            payload={
                "exit_code": driver_result.exit_code,
                "status": status,
                "truncated": driver_result.truncated,
            },
        )

        return ExecutionResult(
            execution_id=exec_id,
            output=driver_result.output,
            exit_code=driver_result.exit_code,
            truncated=driver_result.truncated,
            output_path=output_path,
            duration_ms=duration_ms,
        )

    async def get(self, execution_id: str) -> Execution:
        return await self._meta.get_execution(execution_id)

    async def list(self, workspace_id: str) -> list[Execution]:
        return await self._meta.list_executions(workspace_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _spill_overflow(self, workspace_id: str, exec_id: str, output: str) -> str:
        """Write buffered exec output to ``.forge/exec/<exec_id>.log``."""
        ws_root = self._workspace_store.path(workspace_id)
        target_dir = ws_root / FORGE_META_DIR / "exec"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{exec_id}.log"
        target.write_text(output, encoding="utf-8")
        # Return a workspace-relative path so the value is portable.
        return f"{FORGE_META_DIR}/exec/{exec_id}.log"

    async def _publish(
        self,
        kind: str,
        exec_id: str,
        workspace_id: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        await self._events.publish(
            ForgeEvent(
                kind=kind,
                workspace_id=workspace_id,
                execution_id=exec_id,
                payload=payload or {},
            )
        )


def _prepare_command(req: ExecRequest) -> list[str]:
    """Convert an ``ExecRequest`` into the argv the driver runs.

    ``shell=True`` wraps the command in ``sh -c``, joining tokens with spaces
    so ``["echo", "hi"]`` becomes ``["sh", "-c", "echo hi"]``. Callers that
    want precise quoting should pre-shape the string themselves.
    """
    if not req.command:
        raise ValueError("ExecRequest.command must be non-empty")
    if req.shell:
        joined = " ".join(req.command)
        return ["sh", "-c", joined]
    return list(req.command)


def _classify_status(exit_code: int) -> str:
    if exit_code == 0:
        return "succeeded"
    if exit_code == 124:  # driver's timeout sentinel
        return "timed_out"
    return "failed"


def _result_from_terminal_execution(
    existing: Execution,
    workspace_store: WorkspaceStore,
    workspace_id: str,
) -> ExecutionResult:
    """Reconstruct an :class:`ExecutionResult` from a terminal ``Execution`` row.

    On idempotency hit we only have metadata. Re-read the overflow log if it
    exists so callers get the same shape as a fresh run.
    """
    output = ""
    if existing.output_path:
        try:
            path = workspace_store.path(workspace_id) / existing.output_path
            output = path.read_text(encoding="utf-8")
        except OSError:
            output = ""
    exit_code = existing.exit_code if existing.exit_code is not None else -1
    duration = 0
    if existing.started_at and existing.finished_at:
        duration = int((existing.finished_at - existing.started_at).total_seconds() * 1000)
    return ExecutionResult(
        execution_id=existing.id,
        output=output,
        exit_code=exit_code,
        truncated=existing.truncated,
        output_path=existing.output_path,
        duration_ms=duration,
    )


__all__ = ["ExecutionService"]
