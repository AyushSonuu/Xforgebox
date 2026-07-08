# MVP Implementation Notes (living)

Companion to [mvp-design.md](mvp-design.md) and [mvp-implementation-plan.md](mvp-implementation-plan.md). The plan file lists feature scopes; this file tracks design **amendments** and **branch progress** as the MVP is built.

The full approved branch plan (with per-branch exit criteria) lives at `~/.claude/plans/goofy-napping-music.md`.

## Design amendments

### A1 — Runtime is session-oriented, not container-oriented

**When:** during branch 02 prep, after architectural review.

**Change:** the pool and driver expose a `RuntimeSession` abstraction, not a raw container lease. The execution service asks the pool for a session by *workspace + image*; the driver decides how to satisfy that (docker `--workdir` today, Firecracker block-device attach tomorrow, K8s PVC in V3). Nothing outside `drivers/` sees container IDs or bind-mount paths.

**Shape:**

```python
async with pool.session(workspace_id="ws_123", image="python:3.14-slim") as sess:
    result_1 = await sess.exec(["python", "main.py"])
    result_2 = await sess.exec(["pytest", "-q"])
```

**Consequences that flow from this:**

- Every command runs with `cwd=/workspace` (singular) regardless of runtime.
- Docker driver installs a tiny `/usr/local/bin/forge-run` helper that does `cd "$FORGE_WORKSPACE_DIR" && exec "$@"`; `docker exec` invokes `forge-run <user-cmd>` with `FORGE_WORKSPACE_DIR=/workspaces/<ws-id>` injected. User code never sees the multi-workspace `/workspaces` tree in its own path.
- Sessions are burst-friendly: a Deep Agents turn holds one session across multiple tool calls, amortizing lease overhead.
- V2 Firecracker driver plugs in by satisfying the same `RuntimeSession` protocol — no changes to services / SDK / LangChain adapter.
- V3 K8s / remote-worker drivers do likewise (schedule pod-with-PVC, sync-then-acquire).

### A2 — Baseline Python is 3.14

**When:** branch 02 prep.

**Change:** `requires-python = ">=3.14"`; `.python-version` pinned; CI installs 3.14; ruff `target-version = "py314"`; mypy `python_version = "3.14"`. Reason: operator directive.

## Branch progress

| # | Branch | Status | Merged |
|---|---|---|---|
| 01 | `feat/01-scaffold` | done | main |
| 02a | `feat/02a-python314-and-session-design` | done | main |
| 02 | `feat/02-metastore` | done | main |
| 03 | `feat/03-workspace-store` | done | main |
| 04 | `feat/04-docker-driver` | done | main |
| 05 | `feat/05-pool` | done | main |
| 06 | `feat/06-executions` | done | main |
| 08 | `feat/08-snapshots` | done | main |
| 07 | `feat/07-http` | done | main |
| 09/10/11/12 | `feat/09-sdk-10-langchain-11-cli-12-demo` | done | main |

## Amendment A3 — Deep-Agents `BaseSandbox` used directly

**When:** wave 2, branch 10.

**Change:** the LangChain adapter (`forge.langchain.ForgeSandbox`) subclasses
:class:`deepagents.backends.sandbox.BaseSandbox` rather than defining a
Forge-flavoured ad-hoc protocol. `BaseSandbox` already provides defaults for
`ls`/`read`/`write`/`edit`/`glob`/`grep` implemented on top of the abstract
`execute()` / `upload_files()` / `download_files()` primitives, so the adapter
only supplies those three plus the `id` property. Forge is async-native, so we
also override `aexecute` / `aupload_files` / `adownload_files` to avoid the
default `asyncio.to_thread` wrapping.

Consequences:
- `create_deep_agent(model=..., backend=ForgeSandbox(...))` works with zero
  glue code.
- Every future Deep-Agents backend feature (e.g. new file operations) is
  inherited for free.
- We keep the door open for a future `deepagents.middleware`-based Forge
  workflow.
| 05 | `feat/05-pool` | queued | — |
| 06 | `feat/06-executions` | queued | — |
| 07 | `feat/07-http` | queued | — |
| 08 | `feat/08-snapshots` | queued | — |
| 09 | `feat/09-sdk` | queued | — |
| 10 | `feat/10-langchain` | queued | — |
| 11 | `feat/11-cli` | queued | — |
| 12 | `feat/12-hardening` | queued | — |
