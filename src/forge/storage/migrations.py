"""Embedded schema migrations for the Forge metastore.

We keep migrations inline (no external tool) because:

* The metastore is single-file SQLite in every deployment topology (dev laptop,
  daemon, container CI).
* All statements are idempotent (``CREATE TABLE IF NOT EXISTS``); replaying the
  full list on every start is cheap and matches how the tests expect the store
  to bootstrap.

If the schema evolves later, add a new ``_migration_XXXX_...`` string and append
it to ``MIGRATIONS``. Each entry is executed as a single script under a
transaction.
"""
from __future__ import annotations

from typing import Final

_MIGRATION_0001_INIT: Final = """
CREATE TABLE IF NOT EXISTS workspaces (
    id            TEXT PRIMARY KEY,
    spec_json     TEXT NOT NULL,
    status        TEXT NOT NULL,
    name          TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status);

CREATE TABLE IF NOT EXISTS executions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    environment_id  TEXT,
    command_json    TEXT NOT NULL,
    shell           INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    exit_code       INTEGER,
    truncated       INTEGER NOT NULL DEFAULT 0,
    output_path     TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    idempotency_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_executions_workspace
    ON executions(workspace_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_idem
    ON executions(workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS snapshots (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT,
    format       TEXT NOT NULL DEFAULT 'tar.zst',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    parent_id    TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_workspace ON snapshots(workspace_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    content_type TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_workspace ON artifacts(workspace_id);

CREATE TABLE IF NOT EXISTS scope_bindings (
    scope_kind   TEXT NOT NULL,
    scope_key    TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (scope_kind, scope_key)
);

CREATE TABLE IF NOT EXISTS pool_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    image        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pool_events_image_ts
    ON pool_events(image, ts);
"""


MIGRATIONS: Final[tuple[str, ...]] = (_MIGRATION_0001_INIT,)


__all__ = ["MIGRATIONS"]
