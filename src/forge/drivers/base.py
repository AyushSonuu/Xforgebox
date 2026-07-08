"""Runtime driver protocol + shared value objects.

The pool composes a ``RuntimeDriver`` with a workspace binding to produce a
``RuntimeSession`` — the sole cross-boundary contract that services, SDK, and
the LangChain adapter depend on. See amendment A1 in
``docs/mvp-implementation-notes.md``.

Design rules that follow:

- No caller outside ``forge.drivers`` and ``forge.pool`` may reference
  container IDs, bind-mount paths, or on-host workspace paths.
- Every user command runs with ``cwd=/workspace`` inside the runtime, regardless
  of which driver is in use.
- Drivers translate a ``workspace_id`` into their native storage attachment
  (docker: bind-mount + workdir; firecracker later: block device / virtio-fs;
  k8s later: PVC). Callers just quote a workspace id + image.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from forge.models import ExecutionResult, LogEvent, ResourceLimits, RuntimeCapabilities


@dataclass(frozen=True, slots=True)
class Mount:
    """A host-to-container mount declaration.

    Only the docker driver reads this today; other drivers will translate it
    into their own storage attachment model. Kept intentionally small — no
    per-mount options — because MVP only needs one rw bind mount.
    """

    source: str
    """Absolute host path."""

    target: str
    """Absolute in-runtime path."""

    read_only: bool = False


@dataclass(slots=True)
class EnvironmentHandle:
    """An opaque handle to a running runtime instance.

    The ``id`` is meaningful only to the driver that produced it. The pool
    stores handles in its idle queue; nothing else opens them.
    """

    id: str
    image: str
    driver: str
    runtime_labels: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class RuntimeSession(Protocol):
    """A workspace-scoped execution channel over a running runtime instance.

    Instances are produced by :meth:`forge.pool.ContainerPool.session` and are
    the ONLY object callers hold. ``exec()`` and ``stream_exec()`` may be
    invoked repeatedly for a burst of tool calls; the session is released back
    to the pool when the context manager exits.
    """

    workspace_id: str
    image: str

    async def exec(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> ExecutionResult:
        """Run one command and wait for completion.

        Returns a structured :class:`~forge.models.ExecutionResult` — command
        failures (non-zero exit) DO NOT raise. Only infrastructure errors do.
        """
        ...

    def stream_exec(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[LogEvent]:
        """Run a command and stream stdout/stderr line events."""
        ...


@runtime_checkable
class RuntimeDriver(Protocol):
    """Low-level runtime interface used by the pool.

    Consumers never see this — they use :class:`RuntimeSession` via
    ``pool.session(...)``. The protocol is public here so alternate drivers
    (firecracker in V2, k8s in V3) plug in.
    """

    name: str

    def capabilities(self) -> RuntimeCapabilities:
        """Feature flags the driver supports (used by capability dispatch)."""
        ...

    async def create_environment(
        self,
        *,
        image: str,
        mounts: list[Mount],
        resources: ResourceLimits | None = None,
    ) -> EnvironmentHandle:
        """Boot a new runtime instance ready to accept execs."""
        ...

    async def destroy_environment(self, environment_id: str) -> None:
        """Stop + delete the runtime instance."""
        ...

    async def exec_in_environment(
        self,
        environment_id: str,
        command: list[str],
        *,
        workspace_id: str,
        env: dict[str, str],
        timeout_seconds: float | None,
        max_output_bytes: int,
    ) -> ExecutionResult:
        """Run one command, buffering stdout/stderr up to ``max_output_bytes``."""
        ...

    def stream_exec_in_environment(
        self,
        environment_id: str,
        command: list[str],
        *,
        workspace_id: str,
        env: dict[str, str],
        timeout_seconds: float | None,
    ) -> AsyncIterator[LogEvent]:
        """Stream one command's stdout/stderr as they arrive."""
        ...

    async def health_check(self, environment_id: str) -> bool:
        """Cheap liveness probe — returns True if the instance still runs execs."""
        ...


__all__ = [
    "EnvironmentHandle",
    "Mount",
    "RuntimeDriver",
    "RuntimeSession",
]
