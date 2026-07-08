"""``forged`` — the Forge daemon entrypoint.

Runs the FastAPI app via uvicorn. Accepts a few knobs on the CLI: bind
address, port, pool sizing, data root. Configuration precedence:

1. CLI flag
2. Environment variable (``FORGE_*``)
3. Default in :mod:`forge.config`
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import typer
import uvicorn

from forge.config import DEFAULT_IMAGE, ForgeConfig, make_config
from forge.models import PoolConfig
from forge.server.app import create_app

app = typer.Typer(help="Forge daemon.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", envvar="FORGE_HOST"),
    port: int = typer.Option(8787, envvar="FORGE_PORT"),
    data_root: Path | None = typer.Option(None, envvar="FORGE_DATA_ROOT"),
    image: str = typer.Option(DEFAULT_IMAGE, envvar="FORGE_DEFAULT_IMAGE"),
    pool_min_idle: int = typer.Option(1, envvar="FORGE_POOL_MIN_IDLE"),
    pool_max: int = typer.Option(8, envvar="FORGE_POOL_MAX"),
    pool_idle_ttl: int = typer.Option(600, envvar="FORGE_POOL_IDLE_TTL"),
    exec_timeout: int = typer.Option(120, envvar="FORGE_EXEC_TIMEOUT"),
    log_level: str = typer.Option("info", envvar="FORGE_LOG_LEVEL"),
) -> None:
    """Boot the daemon and serve HTTP until interrupted."""
    logging.basicConfig(level=log_level.upper())
    cfg = _build_config(
        data_root=data_root,
        image=image,
        pool_min_idle=pool_min_idle,
        pool_max=pool_max,
        pool_idle_ttl=pool_idle_ttl,
        exec_timeout=exec_timeout,
    )
    application = create_app(config=cfg)
    uvicorn.run(application, host=host, port=port, log_level=log_level)


def _build_config(
    *,
    data_root: Path | None,
    image: str,
    pool_min_idle: int,
    pool_max: int,
    pool_idle_ttl: int,
    exec_timeout: int,
) -> ForgeConfig:
    pool = PoolConfig(
        image=image,
        min_idle=pool_min_idle,
        max_size=pool_max,
        idle_ttl_seconds=pool_idle_ttl,
        exec_timeout_seconds=exec_timeout,
    )
    base = data_root if data_root else Path(os.environ.get("FORGE_DATA_ROOT", "")) or None
    return make_config(base, default_pool=pool)


def main() -> None:
    """Console-script entrypoint (see ``pyproject.toml``)."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
