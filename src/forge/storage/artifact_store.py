"""On-disk artifact storage.

Each artifact lives under ``{data_root}/artifacts/<artifact_id>/<basename>``
so the original filename survives export. The metastore row records the
workspace-relative source path plus content type.
"""
from __future__ import annotations

import mimetypes
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from forge.errors import ArtifactError
from forge.models import Artifact

_CHUNK = 64 * 1024


class ArtifactStore:
    """Lifecycle for exported files. Format-neutral: keep the bytes as-is."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        *,
        workspace_id: str,
        source: Path,
        rel_path: str,
        content_type: str | None = None,
    ) -> Artifact:
        """Copy ``source`` into a new artifact directory + return metadata."""
        if not source.is_file():
            raise ArtifactError(f"source is not a file: {source}")
        artifact_id = f"art_{uuid.uuid4().hex[:16]}"
        target_dir = self._root / artifact_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / source.name
        try:
            shutil.copyfile(source, target)
        except OSError as e:
            # Clean up so we don't leak an empty dir.
            shutil.rmtree(target_dir, ignore_errors=True)
            raise ArtifactError(f"failed to export artifact: {e}") from e
        ct = content_type or mimetypes.guess_type(source.name)[0]
        return Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            path=rel_path,
            size_bytes=target.stat().st_size,
            content_type=ct,
            created_at=datetime.now(UTC),
        )

    def path(self, artifact_id: str, filename: str) -> Path:
        return self._root / artifact_id / filename

    def delete(self, artifact_id: str) -> None:
        target = self._root / artifact_id
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)

    async def read(self, artifact_id: str, filename: str) -> AsyncIterator[bytes]:
        """Async iterator over the artifact bytes in ``_CHUNK`` slices."""
        target = self._root / artifact_id / filename
        if not target.is_file():
            raise ArtifactError(f"artifact not found: {artifact_id}/{filename}")
        # Small enough to block on — file reads on local SSD are fast, and
        # we yield after every chunk to let the event loop breathe.
        with target.open("rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    return
                yield chunk


__all__ = ["ArtifactStore"]
