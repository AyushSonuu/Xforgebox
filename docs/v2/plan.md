# Forge V2 plan

**Purpose:** V2 turns Forge from a working single-tenant local runtime into a production-ready sandbox for AI agents. The changes fall into three buckets: **isolation** (Firecracker), **operability** (auth, quotas, retention, deploy recipe), and **SDK parity** (align with Modal / E2B / Runloop patterns identified in [sdk-parity.md](sdk-parity.md)).

This document is the branch plan. The **how** for each item lives in [driver-design.md](driver-design.md) (Firecracker specifics) and [sdk-parity.md](sdk-parity.md) (industry-pattern rationale).

**Guiding principle from the MVP still applies:** merge to `main` in small, independently reviewable slices. Each branch ships tests + docs + a demo it enables.

---

## V2 exit criteria

Concrete definition of done:

1. **Firecracker driver passes the same 12 integration tests as the Docker driver.** Full 127-test suite must be green with either driver.
2. **`forged` runs behind bearer-token auth by default.** Anonymous access is opt-in via `--anonymous-ok` flag.
3. **Per-workspace disk quotas enforced by the kernel** — not "we noticed you were over budget and stopped writes."
4. **Deploy recipe** (Terraform / cloud-init / docker-compose) that a competent operator can `git clone && make deploy` and end up with a running daemon in ~10 minutes.
5. **Snapshot-based fan-out / branching** works end-to-end (E2B / Runloop pattern).
6. **`pause` / `resume` lifecycle** — idle workspaces don't hold live compute (E2B / Fly pattern).
7. **SDK conformance suite** — Modal-style + E2B-style API shapes both pass a public conformance test we ship.

None of these individually requires a big-bang release. Each is one PR / one branch.

---

## Branch plan

Numbered continuing from V1 (branches 01-12). Broken into three tracks that can proceed loosely in parallel after F1 lands, and the completed V1 branches are on `main`.

### Track F — Firecracker driver + microVM foundation

| # | Branch | Scope | Depends on |
|---|---|---|---|
| **F1** | `feat/13-firecracker-scaffold` | `FirecrackerDriver` skeleton — `RuntimeDriver` protocol implementation with `NotImplementedError` bodies. Adds `firecracker-python-sdk` dependency. Tests: contract-only (importable, capabilities correct). Deliverable: driver registrable in the config, but every call raises. Lets Track T + Track S proceed with no blocking. | V1 merged |
| **F2** | `feat/14-firecracker-guest-agent` | Build `forge-guest` (Go, static binary). Implements `POST /exec`, `POST /exec/stream`, `GET /health` over vsock. Ships as a compiled artifact in the Python wheel + a container image for Docker-based dev. Tests: unit tests + integration test that runs `forge-guest` in a Docker container and exercises the HTTP API. | F1 |
| **F3** | `feat/15-firecracker-image-cache` | OCI image → rootfs.ext4 conversion. `ImageCache.get_or_build(image)` with LRU eviction. Baked kernel `vmlinux.bin` distributed with the Python wheel. Tests: cache hit/miss, digest stability, eviction policy. | F1 |
| **F4** | `feat/16-firecracker-boot-and-exec` | The load-bearing branch. `create_environment` boots a real microVM, `exec_in_environment` runs a real command over vsock. Copies the 12 Docker driver tests to `test_firecracker_driver.py` — all must pass. Ships behind a `--driver firecracker` daemon flag; Docker remains default. | F2 + F3 |
| **F5** | `feat/17-firecracker-per-workspace-volumes` | Per-workspace ext4 volume attached per-microVM. Eliminates the "peer workspace visibility" tenancy caveat. `WorkspaceVolumeManager` handles ensure/attach/detach. Changes the pool model: each microVM is bound to one workspace for its lifetime (E2B model). Tests: cross-workspace isolation on Firecracker, kernel-enforced quota, volume lifecycle. | F4 |
| **F6** | `feat/18-firecracker-snapshots` | Base-image microVM snapshots for fast boot. On first boot of image X, snapshot the post-boot state; subsequent boots restore from that snapshot (~50ms vs ~150ms fresh). Also enables snapshot-based fan-out (see S1). Tests: snapshot roundtrip, boot-from-snapshot timing, cache invalidation on image change. | F5 |
| **F7** | `feat/19-firecracker-networking` | TAP + iptables per-microVM. Default: isolated (no egress). Optional: bridged with allowlist. Wires into `ResourceLimits` via a new `network: NetworkPolicy` field. Tests: isolated VM can't reach 8.8.8.8; allowlisted VM can hit `api.openai.com` but not `169.254.169.254`. | F5 |

### Track T — Tenancy, operability, and pre-deploy hardening

Can start immediately after V1; only needs API surface to be stable. Not blocked on Firecracker.

| # | Branch | Scope | Depends on |
|---|---|---|---|
| **T1** | `feat/20-bearer-auth-and-tenants` | Bearer-token middleware for HTTP API. `Workspace.metadata["tenant_id"]` becomes load-bearing. Route-level tenant scoping — a workspace only shows in `list()` and only accepts `exec()` from the caller with the same tenant ID. Tests: cross-tenant access forbidden; unauthenticated calls return 401. | V1 |
| **T2** | `feat/21-expires-at-and-retention` | `Workspace.expires_at` in the model. Background retention worker: workspaces idle > `idle_ttl` → deleted (or paused, if F6 landed); snapshots older than `snapshot_ttl_days` → deleted; artifacts older than `artifact_ttl_days` → deleted. Tests: retention triggers, dry-run mode. | V1 |
| **T3** | `feat/22-config-driven-prewarm` | `warm_images: ["python:3.14-slim", "node:22-slim"]` in `ForgeConfig`, honored at `pool.start()`. `forged serve --warm-image python:3.14-slim --warm-image node:22-slim` on the CLI. Tests: N warm containers exist within 2s of start per image. | V1 |
| **T4** | `feat/23-streaming-callbacks` | `sandbox.exec(cmd, on_stdout=fn, on_stderr=fn)` as an alternative to the async-iterator style. Wraps existing `stream_exec`. Same on the LangChain adapter. Tests: callbacks fire in order; back-pressure works. | V1 |
| **T5** | `feat/24-from-id-discovery` | `Forge.workspaces.get(id)` and `Forge.workspaces.list()` promoted to top-level ergonomic patterns. Adds `Forge.snapshots.get(id)`, `Forge.artifacts.get(id)`. Tests: discovery roundtrip. | V1 |
| **T6** | `feat/25-tunnels-primitive` | Expose an in-workspace port as an HTTPS URL. Reverse-proxy layer in the daemon that maps `<workspace-id>.<forged-host>:443` → workspace_port. Tests: HTTPS termination + port forwarding. **Only useful with Firecracker (F4).** | F4 |
| **T7** | `feat/26-quotas-and-loopback-volumes` | Per-workspace loopback ext4 files, mounted at workspace-create time via shared/rshared bind propagation. Enforces hard disk quotas even on the Docker driver. Tests: agent that runs `dd if=/dev/zero of=big bs=1M count=99999` fills a 1GB volume but not the host. | V1 |
| **T8** | `feat/27-deploy-recipe` | `deploy/` folder: Dockerfile for `forged`, systemd unit, docker-compose stack with Traefik + auth, cloud-init script, Terraform module for AWS/GCP. Tests: manual smoke on a fresh EC2 c6i.xlarge. Docs: [Deployment guide](../deployment/) (create). | T1 + T7 |

### Track S — Snapshot semantics + pause/resume + fan-out

The "make it look like E2B" track. Requires F6 (Firecracker snapshots) for the interesting bits; some parts can ship on Docker driver first.

| # | Branch | Scope | Depends on |
|---|---|---|---|
| **S1** | `feat/28-snapshot-fork` | `forge.snapshots.fork(snap_id, name=)` creates a new workspace booted from a snapshot. On Docker: restore tar.zst into a new workspace (already works today; expose as `fork`). On Firecracker: boot microVM from snapshot with copy-on-write divergence. Tests: N agents share a starter state. | V1 (Docker), F6 (Firecracker) |
| **S2** | `feat/29-pause-resume-lifecycle` | `workspace.pause()` freezes state. `workspace.resume()` brings it back. On Docker: no-op (containers destroyed on release anyway; state was already on disk). On Firecracker: pause the microVM, snapshot memory + disk, later restore. Idle-timeout auto-pause (E2B pattern). Tests: pause roundtrip preserves running-process state (Firecracker) or workspace filesystem state (Docker fallback). | F6 |
| **S3** | `feat/30-snapshot-based-prewarm` | Prewarm the pool by *restoring from a base snapshot* instead of fresh-booting. `min_idle_from_snapshot=True` uses cached snapshots. Faster than fresh boot on Firecracker; N/A on Docker. Tests: prewarm latency < snapshot restore + guest ready. | F6 |

### Track Q — Quality-of-life for V2 users

Small, independent, high-value work items. Can be interleaved with any of the above.

| # | Branch | Scope | Depends on |
|---|---|---|---|
| **Q1** | `feat/31-openapi-and-typescript-client` | Export OpenAPI from FastAPI. Generate TypeScript client so JS/TS agents can hit `forged` without our Python SDK. | V1 |
| **Q2** | `feat/32-prometheus-metrics` | `/metrics` endpoint. Pool stats, exec durations, retention events, auth failures. | T1 |
| **Q3** | `feat/33-postgres-metastore` | `MetaStore` implementation on top of `asyncpg`. Same interface. Enables multi-daemon deployments. Tests: identical test suite runs against both backends. | V1 |
| **Q4** | `feat/34-s3-snapshot-artifact-stores` | `SnapshotStore` + `ArtifactStore` implementations on top of `aioboto3`. Two-tier: local SSD cache + async S3 mirror. Enables cross-daemon durability. Tests: roundtrip via mocked S3 (moto). | V1 |
| **Q5** | `feat/35-egress-policy` | `NetworkPolicy` allowlist for `pip install`, `git clone`, LLM providers. Firecracker: enforced by iptables. Docker: enforced by `--network` mode + user-defined network with iptables. Tests: allowed hosts reachable; others refused. | F7 (Firecracker), V1 (Docker) |

---

## Suggested merge order

Not strictly enforced, but this order minimizes coupling:

1. Land **T1** (auth) first — everything else assumes it's there.
2. Then **T3** (prewarm) + **T4** (streaming callbacks) + **T5** (discovery) — small, independent SDK quality wins.
3. **T7** (loopback quotas) can go anytime — Docker-only, easy win.
4. **F1** (Firecracker scaffold) — unlocks the whole F track.
5. **F2** + **F3** in parallel (guest agent + image cache — both feed F4).
6. **F4** — the moment Firecracker actually works.
7. **F5** — per-workspace volumes, eliminates the tenancy caveat.
8. **F6** + **F7** — snapshots and networking.
9. **S1** + **S2** + **S3** — the E2B-style lifecycle.
10. **T2** (retention), **T6** (tunnels), **T8** (deploy recipe) — the final production polish.
11. **Q1-Q5** — interleave anywhere.

Total estimated size: 25-30 branches, similar cadence to V1 (which was 12 branches). Bigger branches (F4, F5) will take ~1 week; smaller ones (T3, T4) are single-day.

---

## Non-goals for V2

Explicitly kept out of scope, listed so we're not lured into scope creep:

- **Distributed workers / scheduler.** V3 territory ([../v3-design.md](../v3-design.md)).
- **Kubernetes driver.** V3.
- **GPU scheduling.** V3.
- **WASM driver.** Speculative; no user demand yet.
- **Multi-agent coordination primitives** (Runloop's Axon/Broker). Belongs above Forge, not inside it. See [sdk-parity.md § reject](sdk-parity.md#what-forge-should-deliberately-reject).
- **Built-in agent framework.** Forge is the runtime, not the framework. LangChain / Deep-Agents integration is the adapter, not a rewrite.
- **Custom image DSL.** Users bring their own Dockerfile — Runloop's stance.
- **Managed control plane.** Forge is self-hostable; we ship a Helm chart + Terraform module (T8), not a SaaS.

---

## Risk register

Things to watch for during V2. If any of these become blocking, escalate — don't power through.

| Risk | Mitigation |
|---|---|
| Firecracker boot latency exceeds ~250 ms target | Fall back to Docker driver on the fast path; keep Firecracker for isolation-critical workloads |
| `forge-guest` becomes a maintenance sink | Keep it tiny (<500 lines); prefer static binary + simple HTTP; consider adopting `envd` from E2B if it stabilizes |
| Per-workspace ext4 volumes hit loopback device limits | Documented `max_loop=1024` at boot; fall back to XFS project quotas on hosts that have XFS |
| macOS developers can't run V2 locally | Keep Docker driver as macOS dev path; ship a "cloud dev" mode where `forged` runs remote via SSH |
| Auth story doesn't fit OIDC integrations | T1 uses bearer tokens (simplest); document how to put an OIDC-aware proxy in front (Traefik / oauth2-proxy) |
| Postgres migration surprises us | Keep SQLite path forever; make Postgres a plugin, not a replacement |
| SDK breaking changes annoy V1 users | Bump to `0.2.0` for V2; keep V1's shape as much as possible; document migration |

---

## What V3 looks like (context only — not being planned here)

Just so V2 decisions don't accidentally close doors on V3:

- **Distributed workers**: separate `worker` binary that reports capacity to a central control plane; scheduler picks a worker per session. Postgres metastore + S3 stores + K8s driver = the natural composition.
- **K8s driver**: `RuntimeDriver` implementation that schedules pods.
- **Policy engine**: OPA-style rules for image allowlist, egress, resource caps.
- **Audit + observability**: structured events → durable queue → Grafana / Datadog / whatever.

V3 will care about *scheduling* and *policy*. V2 is what gets us to production for a single tenant / single-org first.

---

## Related reading

- [driver-design.md](driver-design.md) — the technical companion for Track F.
- [sdk-parity.md](sdk-parity.md) — the SDK research that informs Track T + S.
- [../architecture/overview.md](../architecture/overview.md) — the shape we're preserving.
- [../architecture/pool-and-runtime-session.md](../architecture/pool-and-runtime-session.md) — the abstraction we're extending.
- [../v2-design.md](../v2-design.md) — the original V2 sketch (this doc supersedes it).
- [../v3-design.md](../v3-design.md) — the eventual V3 direction.
