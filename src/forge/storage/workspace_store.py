"""On-disk workspace directory management.

The workspace store owns the *host* filesystem layout for workspaces. It is
deliberately dumb: it creates, resolves, and removes directories. All file
I/O within a workspace goes through :class:`forge.services.files_service.FilesService`
so path-escape checks are applied uniformly.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from forge.errors import WorkspaceError

# Subdirectory reserved for Forge-internal artifacts (execution overflow logs,
# housekeeping metadata). Not visible to user commands because they run with
# cwd=<ws_root> and cannot reach here through normal paths without escaping,
# which the files service rejects.
FORGE_META_DIR = ".forge"


class WorkspaceStore:
    """Manages the ``<data_root>/workspaces/<ws-id>`` directory tree.

    The store is intentionally synchronous — creating/removing a handful of
    directories is dominated by syscalls, and hoisting them to the executor
    only obscures failures.
    """

    def __init__(self, root: Path) -> None:
        """Root is the ``workspaces`` directory (already includes the suffix)."""
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Absolute host path to the workspaces root."""
        return self._root

    def path(self, workspace_id: str) -> Path:
        """Return the host path for a workspace, whether it exists or not."""
        _validate_workspace_id(workspace_id)
        return self._root / workspace_id

    def create(self, workspace_id: str) -> Path:
        """Create the workspace directory + ``.forge/`` subdir.

        Idempotent for the metadata subdir, but raises ``WorkspaceError`` if
        the workspace directory already exists — callers are expected to
        generate fresh UUIDs.
        """
        _validate_workspace_id(workspace_id)
        ws_dir = self._root / workspace_id
        if ws_dir.exists():
            raise WorkspaceError(f"workspace directory already exists: {ws_dir}")
        ws_dir.mkdir(parents=True)
        (ws_dir / FORGE_META_DIR / "exec").mkdir(parents=True, exist_ok=True)
        return ws_dir

    def delete(self, workspace_id: str) -> None:
        """Remove the workspace directory tree. Idempotent."""
        _validate_workspace_id(workspace_id)
        ws_dir = self._root / workspace_id
        if not ws_dir.exists():
            return
        # Follow no symlinks — treat the directory tree as opaque data.
        shutil.rmtree(ws_dir)

    def exists(self, workspace_id: str) -> bool:
        _validate_workspace_id(workspace_id)
        return (self._root / workspace_id).is_dir()


def _validate_workspace_id(workspace_id: str) -> None:
    """Reject workspace IDs that would break path resolution."""
    if not workspace_id or "/" in workspace_id or "\\" in workspace_id or workspace_id in {".", ".."}:
        raise WorkspaceError(f"invalid workspace id: {workspace_id!r}")


__all__ = ["FORGE_META_DIR", "WorkspaceStore"]
