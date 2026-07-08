"""``forge`` — the client CLI.

Talks to a running ``forged`` over HTTP. All commands accept ``--url`` (or
``FORGE_URL`` env) to point at a specific daemon. Table output uses ``rich``
where available; falls back to plain text otherwise.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import typer

from forge import __version__
from forge.client import Forge

app = typer.Typer(help="Forge client CLI.", add_completion=False)
workspace_app = typer.Typer(help="Workspace lifecycle commands.")
files_app = typer.Typer(help="Workspace filesystem commands.")
snapshot_app = typer.Typer(help="Snapshot commands.")
artifact_app = typer.Typer(help="Artifact commands.")
pool_app = typer.Typer(help="Pool introspection.")

app.add_typer(workspace_app, name="workspace")
app.add_typer(files_app, name="files")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(artifact_app, name="artifact")
app.add_typer(pool_app, name="pool")


DEFAULT_URL = "http://127.0.0.1:8787"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _forge(url: str) -> Forge:
    return Forge(url)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed Forge version."""
    typer.echo(__version__)


@app.command()
def health(url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL")) -> None:
    """Ping ``/healthz`` and print the JSON reply."""
    async def _inner() -> None:
        forge = _forge(url)
        try:
            stats = await forge.pool_status()
            typer.echo(json.dumps([s.model_dump(mode="json") for s in stats], indent=2))
        finally:
            await forge.close()

    _run(_inner())


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


@workspace_app.command("create")
def workspace_create(
    image: str = typer.Option("python:3.14-slim", "--image", "-i"),
    name: str | None = typer.Option(None, "--name", "-n"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    """Create a new workspace and print its id."""
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.create(image=image, name=name)
            typer.echo(ws.id)
        finally:
            await forge.close()

    _run(_inner())


@workspace_app.command("list")
def workspace_list(
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            rows = await forge.workspaces.list()
            for w in rows:
                typer.echo(f"{w.id}\t{w.status}\t{w.spec.image}\t{w.name or '-'}")
        finally:
            await forge.close()

    _run(_inner())


@workspace_app.command("delete")
def workspace_delete(
    workspace_id: str,
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            await ws.delete()
        finally:
            await forge.close()

    _run(_inner())


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@files_app.command("read")
def files_read(
    workspace_id: str,
    path: str,
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            r = await ws.files.read(path)
            sys.stdout.write(r.content)
        finally:
            await forge.close()

    _run(_inner())


@files_app.command("write")
def files_write(
    workspace_id: str,
    path: str,
    src: Path,
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            content = src.read_text(encoding="utf-8")
            await ws.files.write(path, content)
        finally:
            await forge.close()

    _run(_inner())


@files_app.command("ls")
def files_ls(
    workspace_id: str,
    path: str = ".",
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            for e in await ws.files.ls(path):
                marker = "d" if e.is_dir else "-"
                typer.echo(f"{marker} {e.size_bytes:>10} {e.path}")
        finally:
            await forge.close()

    _run(_inner())


# ---------------------------------------------------------------------------
# Exec
# ---------------------------------------------------------------------------


@app.command()
def exec_cmd(
    workspace_id: str = typer.Argument(...),
    command: list[str] = typer.Argument(..., help="Command tokens after --"),
    shell: bool = typer.Option(False, "--shell"),
    timeout: float = typer.Option(120.0, "--timeout"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    """Run one command in the workspace. Prints output; exits with its exit code."""
    async def _inner() -> int:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            result = await ws.exec(command, shell=shell, timeout_seconds=timeout)
            sys.stdout.write(result.output)
            return result.exit_code
        finally:
            await forge.close()

    code = _run(_inner())
    raise typer.Exit(code=code)


# Rename `exec` because it shadows the builtin.
app.command(name="exec")(exec_cmd)  # ergonomic alias


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@snapshot_app.command("create")
def snapshot_create(
    workspace_id: str,
    name: str | None = typer.Option(None, "--name", "-n"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            snap = await ws.snapshots.create(name=name)
            typer.echo(snap.id)
        finally:
            await forge.close()

    _run(_inner())


@snapshot_app.command("list")
def snapshot_list(
    workspace_id: str,
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            for s in await ws.snapshots.list():
                typer.echo(f"{s.id}\t{s.size_bytes}\t{s.name or '-'}")
        finally:
            await forge.close()

    _run(_inner())


@snapshot_app.command("restore")
def snapshot_restore(
    snapshot_id: str,
    name: str | None = typer.Option(None, "--name", "-n"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge._transport.restore_snapshot(  # noqa: SLF001
                snapshot_id, name=name, metadata={}
            )
            typer.echo(ws.id)
        finally:
            await forge.close()

    _run(_inner())


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@artifact_app.command("export")
def artifact_export(
    workspace_id: str,
    path: str,
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            ws = await forge.workspaces.get(workspace_id)
            art = await ws.artifacts.export(path)
            typer.echo(art.id)
        finally:
            await forge.close()

    _run(_inner())


@artifact_app.command("download")
def artifact_download(
    artifact_id: str,
    output: Path = typer.Option(..., "--output", "-o"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            stream = await forge._transport.read_artifact(artifact_id)  # noqa: SLF001
            with output.open("wb") as f:
                async for chunk in stream:
                    f.write(chunk)
        finally:
            await forge.close()

    _run(_inner())


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


@pool_app.command("status")
def pool_status(
    image: str | None = typer.Option(None, "--image", "-i"),
    url: str = typer.Option(DEFAULT_URL, envvar="FORGE_URL"),
) -> None:
    async def _inner() -> None:
        forge = _forge(url)
        try:
            stats = await forge.pool_status(image)
            for s in stats:
                typer.echo(
                    f"{s.image}\tidle={s.idle}\tin_use={s.in_use}\ttotal={s.total}\t"
                    f"max={s.max_size}\tleases={s.total_leases}\tkills={s.total_health_kills}"
                )
        finally:
            await forge.close()

    _run(_inner())


# Suppress the unused import warning; base64 stays for future --upload commands.
_ = base64


if __name__ == "__main__":  # pragma: no cover
    app()
