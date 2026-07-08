"""Runtime configuration for the Forge daemon.

Everything that varies between "run under tests / dev / prod" flows through
:class:`ForgeConfig`. The rest of the codebase should accept a ``ForgeConfig``
instance rather than reading environment variables directly.

Layout of ``data_root``::

    <data_root>/
        meta.db                 # SQLite metastore (see storage.meta_store)
        workspaces/<ws-id>/     # per-workspace host directory
        workspaces/<ws-id>/.forge/exec/*.log  # overflow logs
        snapshots/<id>.tar.zst  # snapshot archives (branch 08)
        artifacts/<id>/...      # exported artifacts (branch 08)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from forge.models import PoolConfig

# Default image is Python 3.14-slim to match the daemon baseline (A2). Override
# per-workspace via WorkspaceSpec.image.
DEFAULT_IMAGE = "python:3.14-slim"


@dataclass(slots=True)
class ForgeConfig:
    """Top-level daemon configuration.

    Constructed once by the daemon (or by test fixtures) and passed down to
    every service. All paths are absolute; the constructor resolves relative
    inputs against the current working directory.
    """

    data_root: Path
    """Root on-disk directory. Everything Forge persists lives under here."""

    meta_db_path: Path
    """Path to the SQLite metastore file. Defaults to ``data_root/meta.db``."""

    default_pool: PoolConfig = field(default_factory=lambda: PoolConfig(image=DEFAULT_IMAGE))
    """Sub-pool defaults for images that don't have an explicit override."""

    per_image_pool: dict[str, PoolConfig] = field(default_factory=dict)
    """Per-image ``PoolConfig`` overrides. Missing keys fall back to ``default_pool``."""

    def __post_init__(self) -> None:
        self.data_root = Path(self.data_root).resolve()
        self.meta_db_path = Path(self.meta_db_path).resolve()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def workspaces_root(self) -> Path:
        """Host directory that holds every per-workspace tree."""
        return self.data_root / "workspaces"

    @property
    def snapshots_root(self) -> Path:
        """Host directory that holds snapshot archives."""
        return self.data_root / "snapshots"

    @property
    def artifacts_root(self) -> Path:
        """Host directory that holds exported artifacts."""
        return self.data_root / "artifacts"

    def pool_config_for(self, image: str) -> PoolConfig:
        """Return the ``PoolConfig`` that applies to ``image``.

        Falls back to a copy of :attr:`default_pool` with the image rewritten
        to match the request — that way per-image counters/logs are consistent.
        """
        override = self.per_image_pool.get(image)
        if override is not None:
            return override
        return self.default_pool.model_copy(update={"image": image})

    def ensure_layout(self) -> None:
        """Create the top-level directories if they don't yet exist."""
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.meta_db_path.parent.mkdir(parents=True, exist_ok=True)


def make_config(
    data_root: str | os.PathLike[str] | None = None,
    *,
    default_pool: PoolConfig | None = None,
    per_image_pool: dict[str, PoolConfig] | None = None,
) -> ForgeConfig:
    """Factory that resolves the standard layout under ``data_root``.

    Used by tests, the daemon, and the CLI. ``data_root`` defaults to
    ``$FORGE_DATA_ROOT`` when set, or ``~/.forge`` otherwise.
    """
    if data_root is None:
        env_root = os.environ.get("FORGE_DATA_ROOT")
        base = Path(env_root) if env_root else Path.home() / ".forge"
    else:
        base = Path(data_root)
    base = base.resolve()
    cfg = ForgeConfig(
        data_root=base,
        meta_db_path=base / "meta.db",
        default_pool=default_pool or PoolConfig(image=DEFAULT_IMAGE),
        per_image_pool=dict(per_image_pool or {}),
    )
    cfg.ensure_layout()
    return cfg


__all__ = ["DEFAULT_IMAGE", "ForgeConfig", "make_config"]
