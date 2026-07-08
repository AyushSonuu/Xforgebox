"""Async SQLite metastore for Forge.

Stores everything the daemon needs to remember between restarts:

* ``workspaces`` — the persistent workspace definitions.
* ``executions`` — one row per command run (with idempotency keys).
* ``snapshots`` / ``artifacts`` — export ledgers.
* ``scope_bindings`` — LangChain thread/assistant → workspace mappings.
* ``pool_events`` — an append-only audit trail for pool lifecycle events.

The store is *only* touched from async code paths; every method takes/returns
Pydantic models from :mod:`forge.models` so callers never handle raw rows.

Datetimes are persisted as ISO-8601 strings with tz info preserved. JSON-shaped
columns (spec, metadata, command) are stored as text and re-parsed on read.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import aiosqlite

from forge.errors import ConfigError, NotFoundError
from forge.models import (
    Artifact,
    Execution,
    Snapshot,
    Workspace,
    WorkspaceSpec,
)

from .migrations import MIGRATIONS


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Refuse to silently mis-interpret naive datetimes.
        raise ConfigError("datetime without tzinfo is not accepted by MetaStore")
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Legacy rows: assume UTC — never invent local time.
        dt = dt.replace(tzinfo=UTC)
    return dt


class MetaStore:
    """Async wrapper around a single-file SQLite database.

    Use as an async context manager or via :meth:`connect` / :meth:`close`.
    All methods are coroutines and safe to call from multiple tasks; SQLite
    serializes writes internally, and short-lived aiosqlite transactions keep
    the surface small.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the DB, apply migrations, enable foreign keys."""
        if self._db is not None:
            return
        # Ensure parent dir exists (":memory:" bypasses).
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        for script in MIGRATIONS:
            await self._db.executescript(script)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise ConfigError("MetaStore is not connected")
        return self._db

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    async def create_workspace(self, ws: Workspace) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT INTO workspaces
                (id, spec_json, status, name, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ws.id,
                ws.spec.model_dump_json(),
                ws.status,
                ws.name,
                json.dumps(ws.metadata),
                _iso(ws.created_at),
                _iso(ws.updated_at),
            ),
        )
        await db.commit()

    async def get_workspace(self, workspace_id: str) -> Workspace:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"workspace not found: {workspace_id}")
        return _row_to_workspace(row)

    async def list_workspaces(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[Workspace]:
        db = self._conn()
        sql = "SELECT * FROM workspaces"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_workspace(r) for r in rows]

    async def update_workspace_status(
        self,
        workspace_id: str,
        status: str,
        updated_at: datetime,
    ) -> None:
        db = self._conn()
        cur = await db.execute(
            "UPDATE workspaces SET status = ?, updated_at = ? WHERE id = ?",
            (status, _iso(updated_at), workspace_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"workspace not found: {workspace_id}")

    async def delete_workspace(self, workspace_id: str) -> None:
        db = self._conn()
        cur = await db.execute(
            "DELETE FROM workspaces WHERE id = ?", (workspace_id,)
        )
        await db.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"workspace not found: {workspace_id}")

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------

    async def create_execution(self, ex: Execution) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT INTO executions
                (id, workspace_id, environment_id, command_json, shell, status,
                 exit_code, truncated, output_path, started_at, finished_at,
                 idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ex.id,
                ex.workspace_id,
                ex.environment_id,
                json.dumps(ex.command),
                1 if ex.shell else 0,
                ex.status,
                ex.exit_code,
                1 if ex.truncated else 0,
                ex.output_path,
                _iso(ex.started_at),
                _iso(ex.finished_at),
                ex.idempotency_key,
            ),
        )
        await db.commit()

    async def get_execution(self, execution_id: str) -> Execution:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM executions WHERE id = ?", (execution_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"execution not found: {execution_id}")
        return _row_to_execution(row)

    async def get_execution_by_idempotency(
        self, workspace_id: str, key: str
    ) -> Execution | None:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM executions WHERE workspace_id = ? AND idempotency_key = ?",
            (workspace_id, key),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_execution(row) if row is not None else None

    async def update_execution(self, execution_id: str, **fields: Any) -> None:
        """Partially update an execution row.

        Accepts any subset of writable columns; datetimes are coerced to ISO
        strings and booleans to 0/1. Unknown keys raise ``ConfigError`` to
        catch typos early.
        """
        if not fields:
            return
        allowed = {
            "environment_id",
            "status",
            "exit_code",
            "truncated",
            "output_path",
            "started_at",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ConfigError(f"unknown execution fields: {sorted(unknown)}")

        columns: list[str] = []
        values: list[Any] = []
        for k, v in fields.items():
            columns.append(f"{k} = ?")
            if isinstance(v, datetime):
                values.append(_iso(v))
            elif isinstance(v, bool):
                values.append(1 if v else 0)
            else:
                values.append(v)
        values.append(execution_id)
        db = self._conn()
        cur = await db.execute(
            f"UPDATE executions SET {', '.join(columns)} WHERE id = ?",
            values,
        )
        await db.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"execution not found: {execution_id}")

    async def list_executions(self, workspace_id: str) -> list[Execution]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM executions WHERE workspace_id = ? ORDER BY id ASC",
            (workspace_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_execution(r) for r in rows]

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def create_snapshot(self, s: Snapshot) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT INTO snapshots
                (id, workspace_id, name, format, size_bytes, parent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s.id,
                s.workspace_id,
                s.name,
                s.format,
                s.size_bytes,
                s.parent_id,
                _iso(s.created_at),
            ),
        )
        await db.commit()

    async def get_snapshot(self, snapshot_id: str) -> Snapshot:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"snapshot not found: {snapshot_id}")
        return _row_to_snapshot(row)

    async def list_snapshots(self, workspace_id: str) -> list[Snapshot]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM snapshots WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_snapshot(r) for r in rows]

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    async def create_artifact(self, a: Artifact) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT INTO artifacts
                (id, workspace_id, path, size_bytes, content_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                a.id,
                a.workspace_id,
                a.path,
                a.size_bytes,
                a.content_type,
                _iso(a.created_at),
            ),
        )
        await db.commit()

    async def get_artifact(self, artifact_id: str) -> Artifact:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        return _row_to_artifact(row)

    async def list_artifacts(self, workspace_id: str) -> list[Artifact]:
        db = self._conn()
        async with db.execute(
            "SELECT * FROM artifacts WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_artifact(r) for r in rows]

    # ------------------------------------------------------------------
    # Scope bindings
    # ------------------------------------------------------------------

    async def bind_scope(self, kind: str, key: str, workspace_id: str) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT INTO scope_bindings (scope_kind, scope_key, workspace_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope_kind, scope_key) DO UPDATE SET workspace_id = excluded.workspace_id
            """,
            (kind, key, workspace_id, _iso(datetime.now(UTC))),
        )
        await db.commit()

    async def resolve_scope(self, kind: str, key: str) -> str | None:
        db = self._conn()
        async with db.execute(
            "SELECT workspace_id FROM scope_bindings WHERE scope_kind = ? AND scope_key = ?",
            (kind, key),
        ) as cur:
            row = await cur.fetchone()
        return None if row is None else str(row["workspace_id"])

    # ------------------------------------------------------------------
    # Pool events (audit)
    # ------------------------------------------------------------------

    async def append_pool_event(
        self,
        image: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        ts: datetime | None = None,
    ) -> None:
        db = self._conn()
        await db.execute(
            "INSERT INTO pool_events (ts, image, kind, payload_json) VALUES (?, ?, ?, ?)",
            (
                _iso(ts or datetime.now(UTC)),
                image,
                kind,
                json.dumps(payload or {}),
            ),
        )
        await db.commit()

    async def list_pool_events(
        self, image: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        db = self._conn()
        if image is None:
            sql = "SELECT * FROM pool_events ORDER BY id DESC LIMIT ?"
            params: Iterable[Any] = (limit,)
        else:
            sql = "SELECT * FROM pool_events WHERE image = ? ORDER BY id DESC LIMIT ?"
            params = (image, limit)
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "ts": _parse_iso(r["ts"]),
                "image": r["image"],
                "kind": r["kind"],
                "payload": json.loads(r["payload_json"]),
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Row → model helpers
# ---------------------------------------------------------------------------


def _row_to_workspace(row: aiosqlite.Row) -> Workspace:
    return Workspace(
        id=row["id"],
        spec=WorkspaceSpec.model_validate_json(row["spec_json"]),
        status=row["status"],
        name=row["name"],
        metadata=json.loads(row["metadata_json"]),
        created_at=_parse_iso(row["created_at"]) or datetime.now(UTC),
        updated_at=_parse_iso(row["updated_at"]) or datetime.now(UTC),
    )


def _row_to_execution(row: aiosqlite.Row) -> Execution:
    return Execution(
        id=row["id"],
        workspace_id=row["workspace_id"],
        environment_id=row["environment_id"],
        command=json.loads(row["command_json"]),
        shell=bool(row["shell"]),
        status=row["status"],
        exit_code=row["exit_code"],
        truncated=bool(row["truncated"]),
        output_path=row["output_path"],
        started_at=_parse_iso(row["started_at"]),
        finished_at=_parse_iso(row["finished_at"]),
        idempotency_key=row["idempotency_key"],
    )


def _row_to_snapshot(row: aiosqlite.Row) -> Snapshot:
    return Snapshot(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        format=row["format"],
        size_bytes=row["size_bytes"],
        parent_id=row["parent_id"],
        created_at=_parse_iso(row["created_at"]) or datetime.now(UTC),
    )


def _row_to_artifact(row: aiosqlite.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        workspace_id=row["workspace_id"],
        path=row["path"],
        size_bytes=row["size_bytes"],
        content_type=row["content_type"],
        created_at=_parse_iso(row["created_at"]) or datetime.now(UTC),
    )


__all__ = ["MetaStore"]
