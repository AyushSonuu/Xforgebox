"""Forge implementation of the ``deepagents`` sandbox backend protocol.

Extends :class:`deepagents.backends.BaseSandbox`, so the standard
Deep-Agents shell + filesystem tools work with Forge out of the box:

.. code-block:: python

    from deepagents import create_deep_agent
    from forge.client import Forge
    from forge.langchain import ForgeSandbox

    forge = Forge("http://localhost:8787")
    backend = await ForgeSandbox.acreate(forge=forge, image="python:3.14-slim")
    agent = create_deep_agent(model=..., backend=backend)

``BaseSandbox`` provides default implementations for ``ls`` / ``read`` /
``write`` / ``edit`` / ``glob`` / ``grep`` on top of ``execute()``. Concrete
subclasses only need to supply:

- ``execute(command, *, timeout)`` — run a shell command, return
  :class:`ExecuteResponse` (never raise for command failures).
- ``upload_files(files)`` — batch write bytes into the workspace.
- ``download_files(paths)`` — batch read bytes back out.
- ``id`` — a stable identifier used for cache keys.

Forge is async-native, so we also override the ``a*`` variants to avoid
the default ``asyncio.to_thread`` wrapping.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from forge.client import Forge, WorkspaceHandle
from forge.errors import PathEscapeError, WorkspaceError


class ForgeSandbox(BaseSandbox):
    """Forge-backed :class:`~deepagents.backends.BaseSandbox`.

    Not intended for hostile multi-tenant use — Forge MVP uses a shared
    Docker container pool. Fine for single-tenant / trusted-agent scenarios.
    """

    def __init__(
        self,
        *,
        forge: Forge,
        workspace: WorkspaceHandle,
        max_output_bytes: int = 100_000,
        default_timeout_seconds: int = 120,
    ) -> None:
        # BaseSandbox is an ABC — no meaningful __init__ to call, but we
        # follow the pattern anyway.
        super().__init__()
        self._forge = forge
        self._workspace = workspace
        self._max_output_bytes = max_output_bytes
        self._default_timeout = default_timeout_seconds

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    async def acreate(
        cls,
        *,
        forge: Forge,
        image: str = "python:3.14-slim",
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ForgeSandbox:
        """Create a fresh workspace and wrap it as a sandbox."""
        ws = await forge.workspaces.create(image=image, name=name, metadata=metadata)
        return cls(forge=forge, workspace=ws)

    @classmethod
    async def afrom_workspace(
        cls,
        *,
        forge: Forge,
        workspace_id: str,
    ) -> ForgeSandbox:
        """Attach to an existing workspace by id."""
        ws = await forge.workspaces.get(workspace_id)
        return cls(forge=forge, workspace=ws)

    @classmethod
    async def afrom_thread(
        cls,
        *,
        forge: Forge,
        thread_id: str,
        image: str = "python:3.14-slim",
    ) -> ForgeSandbox:
        """One workspace per thread — tagged with ``thread_id`` in metadata.

        MVP note: this always creates a new workspace. Persistent
        thread-to-workspace binding lookup lives in V2 (see
        ``docs/mvp-design.md`` scoping section).
        """
        return await cls.acreate(
            forge=forge,
            image=image,
            name=f"thread-{thread_id}",
            metadata={"scope": "thread", "thread_id": thread_id},
        )

    @classmethod
    async def afrom_assistant(
        cls,
        *,
        forge: Forge,
        assistant_id: str,
        image: str = "python:3.14-slim",
    ) -> ForgeSandbox:
        """One workspace per assistant — shared across threads."""
        return await cls.acreate(
            forge=forge,
            image=image,
            name=f"assistant-{assistant_id}",
            metadata={"scope": "assistant", "assistant_id": assistant_id},
        )

    # ------------------------------------------------------------------
    # BaseSandbox abstract surface
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Stable identifier — the underlying workspace id."""
        return self._workspace.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Sync execute — runs the async path on a private event loop.

        Deep-Agents uses this for tools that don't take an async caller.
        Cadence is tool-call level (dozens per second at most), so the
        loop-per-call overhead is fine for MVP.
        """
        result: ExecuteResponse = _run_sync(self.aexecute(command, timeout=timeout))
        return result

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> ExecuteResponse:
        """Native-async execute — the preferred path."""
        try:
            result = await self._workspace.exec(
                ["sh", "-c", command],
                shell=False,  # already wrapped in sh -c
                timeout_seconds=float(timeout if timeout is not None else self._default_timeout),
                max_output_bytes=self._max_output_bytes,
            )
        except (WorkspaceError, PathEscapeError) as e:
            # Convert workspace-layer failures into a non-zero response.
            return ExecuteResponse(output=str(e), exit_code=1, truncated=False)
        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        result: list[FileUploadResponse] = _run_sync(self.aupload_files(files))
        return result

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        # BaseSandbox expects per-file partial-success semantics.
        out: list[FileUploadResponse] = []
        for path, content in files:
            items = [{"path": path, "content_b64": base64.b64encode(content).decode()}]
            try:
                written = await self._workspace._t.upload_files(  # noqa: SLF001
                    self._workspace.id, items
                )
                out.append(FileUploadResponse(path=written[0]))
            except (WorkspaceError, PathEscapeError) as e:
                out.append(FileUploadResponse(path=path, error=str(e)))
        return out

    def download_files(
        self, paths: list[str]
    ) -> list[FileDownloadResponse]:
        result: list[FileDownloadResponse] = _run_sync(self.adownload_files(paths))
        return result

    async def adownload_files(
        self, paths: list[str]
    ) -> list[FileDownloadResponse]:
        out: list[FileDownloadResponse] = []
        for path in paths:
            try:
                items = await self._workspace._t.download_files(  # noqa: SLF001
                    self._workspace.id, [path]
                )
                data = base64.b64decode(items[0]["content_b64"])
                out.append(FileDownloadResponse(path=path, content=data))
            except (WorkspaceError, PathEscapeError) as e:
                out.append(FileDownloadResponse(path=path, content=None, error=str(e)))
        return out

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def workspace(self) -> WorkspaceHandle:
        return self._workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_sync(coro: Any) -> Any:
    """Run ``coro`` on an event loop; return its result.

    If there's a running loop (we're inside async code), that's a bug — this
    helper is for the sync entrypoints Deep-Agents calls from tool wrappers.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "ForgeSandbox.execute()/upload_files()/download_files() called from "
        "inside a running event loop. Use aexecute()/aupload_files()/"
        "adownload_files() instead, or ensure the sync entrypoint runs in a "
        "worker thread."
    )


__all__ = ["ForgeSandbox"]
