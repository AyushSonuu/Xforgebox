"""Snapshot archive store — tar streamed through zstandard.

Snapshots are on-disk ``.tar.zst`` files under ``{data_root}/snapshots/``.
The MVP intentionally uses full archives (no incremental / content-addressed
storage) — cheap to implement and easy to reason about. Larger workspaces or
frequent snapshots are a V2 concern.
"""
from __future__ import annotations

import shutil
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import zstandard as zstd

from forge.errors import SnapshotError
from forge.models import Snapshot

# We exclude the transient exec-log directory so overflow logs from prior
# runs don't bloat every snapshot. Anything else under the workspace is fair
# game.
_EXCLUDE_PREFIXES = (".forge/exec/",)

# Zstandard compression level. 3 is a good balance for source trees and
# small artifacts; large binary blobs stay roughly the same size either way.
_ZSTD_LEVEL = 3


class SnapshotStore:
    """Snapshot lifecycle on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> Snapshot:
        """Archive ``workspace_path`` into ``{root}/<snap_id>.tar.zst``.

        Returns a persisted :class:`Snapshot` model. Callers write the
        corresponding metastore row.
        """
        snap_id = f"snap_{uuid.uuid4().hex[:16]}"
        archive_path = self._root / f"{snap_id}.tar.zst"
        try:
            self._write_archive(workspace_path, archive_path)
        except OSError as e:
            raise SnapshotError(f"failed to write snapshot archive: {e}") from e
        size_bytes = archive_path.stat().st_size
        return Snapshot(
            id=snap_id,
            workspace_id=workspace_id,
            name=name,
            format="tar.zst",
            size_bytes=size_bytes,
            parent_id=parent_id,
            created_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, snapshot_id: str, destination: Path) -> None:
        """Extract ``snapshot_id`` into ``destination`` (which must exist and be empty)."""
        archive_path = self._root / f"{snapshot_id}.tar.zst"
        if not archive_path.is_file():
            raise SnapshotError(f"snapshot archive missing: {snapshot_id}")
        if not destination.is_dir():
            raise SnapshotError(f"restore target is not a directory: {destination}")
        try:
            self._read_archive(archive_path, destination)
        except (OSError, tarfile.TarError, zstd.ZstdError) as e:
            raise SnapshotError(f"failed to restore snapshot {snapshot_id}: {e}") from e

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, snapshot_id: str) -> None:
        """Remove the archive. Idempotent."""
        archive_path = self._root / f"{snapshot_id}.tar.zst"
        archive_path.unlink(missing_ok=True)

    def path(self, snapshot_id: str) -> Path:
        return self._root / f"{snapshot_id}.tar.zst"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_archive(self, source: Path, target: Path) -> None:
        """Compress ``source`` (a directory) into ``target``."""
        source = source.resolve()
        if not source.is_dir():
            raise SnapshotError(f"workspace path missing: {source}")

        cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
        with (
            target.open("wb") as raw,
            cctx.stream_writer(raw) as compressor,
            tarfile.open(mode="w|", fileobj=compressor) as tf,
        ):
            for host_path in _iter_files(source):
                rel = host_path.relative_to(source).as_posix()
                if any(rel.startswith(pfx) for pfx in _EXCLUDE_PREFIXES):
                    continue
                tf.add(host_path, arcname=rel, recursive=False)

    def _read_archive(self, archive: Path, destination: Path) -> None:
        dctx = zstd.ZstdDecompressor()
        with (
            archive.open("rb") as raw,
            dctx.stream_reader(raw) as decompressor,
            tarfile.open(mode="r|", fileobj=decompressor) as tf,
        ):
            # Python 3.12+ supports the "data" filter that rejects absolute
            # paths and traversal automatically.
            tf.extractall(destination, filter="data")


def _iter_files(root: Path):  # type: ignore[no-untyped-def]
    """Yield every file/dir under ``root`` in a deterministic order.

    We yield directories too so empty ones round-trip correctly. ``os.walk``
    with sorted output makes archives byte-stable across runs, which helps
    with content-addressed dedup later if we want it.
    """
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        entries = sorted(current.iterdir())
        for entry in entries:
            if entry.is_symlink():
                # Skip symlinks — the workspace root is bind-mounted into the
                # container, so any symlinks users create belong on-host and
                # cross workspace boundaries in a way that is unsafe to snap.
                continue
            if entry.is_dir():
                yield entry
                stack.append(entry)
            else:
                yield entry


def ensure_dir_empty(path: Path) -> None:
    """Convenience for callers restoring into a fresh workspace directory."""
    if any(path.iterdir()):
        # Wipe and recreate so restore lands on a clean slate.
        shutil.rmtree(path)
        path.mkdir()


__all__ = ["SnapshotStore", "ensure_dir_empty"]
