"""``_PooledSession`` — the RuntimeSession implementation the pool hands out.

Wraps an :class:`~forge.drivers.base.EnvironmentHandle` with a workspace
binding + driver reference. Exists as its own module so the pool code can
depend on it without pulling in driver internals.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from forge.drivers.base import EnvironmentHandle, RuntimeDriver
from forge.models import ExecutionResult, LogEvent, PoolConfig


@dataclass(slots=True)
class _PooledSession:
    """Concrete ``RuntimeSession`` handed to callers.

    The pool creates one of these on ``__aenter__`` and releases the
    underlying environment on ``__aexit__``. Instances are single-use: after
    the enclosing ``async with`` exits, further ``exec`` calls raise.
    """

    workspace_id: str
    image: str
    _env: EnvironmentHandle
    _driver: RuntimeDriver
    _config: PoolConfig
    _closed: bool = False

    async def exec(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> ExecutionResult:
        """Run one command inside this session's pooled runtime."""
        if self._closed:
            raise RuntimeError("session is closed")
        timeout = timeout_seconds if timeout_seconds is not None else float(
            self._config.exec_timeout_seconds
        )
        max_out = max_output_bytes if max_output_bytes is not None else self._config.max_output_bytes
        return await self._driver.exec_in_environment(
            self._env.id,
            command,
            workspace_id=self.workspace_id,
            env=env or {},
            timeout_seconds=timeout,
            max_output_bytes=max_out,
        )

    def stream_exec(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[LogEvent]:
        """Stream stdout/stderr for one command as :class:`LogEvent` values."""
        if self._closed:
            raise RuntimeError("session is closed")
        timeout = timeout_seconds if timeout_seconds is not None else float(
            self._config.exec_timeout_seconds
        )
        return self._driver.stream_exec_in_environment(
            self._env.id,
            command,
            workspace_id=self.workspace_id,
            env=env or {},
            timeout_seconds=timeout,
        )

    def _close(self) -> None:
        self._closed = True


__all__ = ["_PooledSession"]
