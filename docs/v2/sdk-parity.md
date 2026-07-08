# SDK parity — what to learn from Modal, E2B, Daytona, Runloop, Fly, and Anthropic

**Purpose:** before we build V2, decide which cross-industry patterns Forge should align with (for user familiarity) and which we deliberately reject (because we're a self-hostable OSS runtime, not a managed cloud). Research done December 2026.

This doc informs the API decisions in [plan.md](plan.md) and [driver-design.md](driver-design.md).

---

## The comparison table

| Platform | Isolation | Primitive | Cold-start (public claim) | Prewarm | FS model | Self-host | OSS | API style |
|---|---|---|---|---|---|---|---|---|
| **Modal** | gVisor (user-space kernel) | `Sandbox` | ~1s container boot | `min_containers`, `buffer_containers` | Volumes + RPC filesystem (`sandbox.filesystem.*`) | ❌ SaaS | ❌ | Async-first, sync-compatible; iterable stdout streams |
| **E2B** | Firecracker microVM | `Sandbox` | ~1s resume from paused | Not publicly disclosed | Ephemeral + pause/snapshot; Templates for build; Volumes (beta) | ✅ via `e2b-dev/infra` repo | ✅ Apache-2.0 | Sync + async parity; two-layer SDK (`e2b` + `e2b_code_interpreter`) |
| **Daytona** | Docs coy; sub-90ms creation suggests microVM | `Sandbox` (formerly "Workspace") | ~90ms creation | Snapshots-as-templates | Stateful; snapshots + volumes + git + object storage | Partial (BYO-Compute); code went private in 2026 | ⚠️ Was OSS; now hosted | Sub-namespaced (`sandbox.process`, `sandbox.fs`, `sandbox.git`); sync + async |
| **Runloop** | microVM (hypervisor unnamed publicly); VM + container in-guest | `Devbox` | <2s for 10GB image | Not documented | Persistent disk; disk-only snapshots; Blueprints from Dockerfile | ❌ SaaS | ❌ | Async recommended; blocking `exec` + non-blocking `exec_async`; Tunnel primitive for HTTPS |
| **Fly.io Machines** | Firecracker microVM | `Machine` (nested under `App`) | "Subsecond" resume from suspended | `min_machines_running` per service | Local NVMe volumes per Machine; block-level snapshots | ❌ SaaS (but code partially open) | Partial | REST API (`api.machines.dev/v1`), no official Python SDK; `fly-replay` header for routing |
| **Anthropic Code Execution** | "Sandboxed container" (details opaque) | `container` (implicit; addressed by ID) | Not disclosed; checkpoints after ~5min idle | Server-managed | Files API (uploads/downloads only, no mount); persists across turns | ❌ SaaS | ❌ | Server-side tool block; `container` as request param; opaque `container_id` with `expires_at` |
| **Forge (today)** | Docker container (shared kernel) | `Workspace` + pooled `Environment` | ~200ms per container | `min_idle`, `max_size` | Persistent host directories; tar.zst snapshots | ✅ Self-host | ✅ | Async-first Python; HTTP daemon; `Forge` + `WorkspaceHandle` sub-resources |

**Detailed research notes for each platform** live at the end of this doc under [Appendix — per-platform notes](#appendix--per-platform-notes).

---

## Consistent patterns across the industry

Some naming and mental-model choices are effectively universal. Aligning with them costs nothing and helps every developer who's touched another sandbox SDK feel at home.

### 1. "Sandbox" is the canonical name

- **Modal**: `Sandbox`
- **E2B**: `Sandbox`
- **Daytona**: `Sandbox` (they renamed from "Workspace" specifically)
- **Runloop**: `Devbox` (only outlier — they use their own term)
- **Anthropic**: "container" (server-owned, hidden)
- **Fly**: `Machine` (they're a general-purpose VM host, not sandbox-specialized)

**Forge's stance:** we use `Workspace` for the persistent-state noun. That's actually correct — most platforms conflate "environment" and "workspace" into one concept (the sandbox), but in Forge they're deliberately different: a workspace lives on disk, environments come and go from the pool. However, our LangChain adapter (which is what most people will first touch) is called `ForgeSandbox` — matching industry convention where it matters. **No change needed.**

### 2. `async` + context manager for lifecycle

Everyone converged on `async with` for lifecycle management:

- E2B: sync + async parity, context-manager close.
- Daytona: `async with AsyncDaytona(config)`.
- Runloop: async recommended, `AsyncRunloopSDK`.
- Modal: async-first, sync auto-generated.

**Forge alignment:** we already have `async with Forge(...)`. Confirmed correct.

### 3. Streaming stdout/stderr with callbacks or iterables

- **Modal**: iterable stdout — `for line in process.stdout: ...`
- **E2B**: callback-style — `commands.run(cmd, on_stdout=..., on_stderr=..., background=True)`
- **Runloop**: streaming callbacks on `exec_async`
- **Anthropic**: input streams as JSON deltas; results arrive whole

**Forge alignment:** we have `RuntimeSession.stream_exec()` returning `AsyncIterator[LogEvent]`. That's the Modal pattern. **Consider also exposing the callback style** for LangChain tools that prefer callbacks — cheap wrapper. Track this in [plan.md](plan.md).

### 4. Snapshot-as-first-class-primitive

Every serious player has snapshots as durable, restorable identity handles:

- **E2B**: pause/snapshot as *default* lifecycle (not stop=destroy).
- **Modal**: `Volume` snapshots + separate `Memory Snapshots` for cold-start.
- **Daytona**: snapshots-as-templates for fast boot.
- **Runloop**: disk-only snapshots for fan-out.
- **Fly**: on-demand + scheduled Volume snapshots with retention policies.
- **Anthropic**: server-side checkpoint after ~5min idle; opaque `container_id` resurrects state.

**Forge status:** we have snapshots, but they're used more like "save state" than "identity handle." Every platform above uses snapshots to accelerate cold-start OR fan out to many copies of a base state. Both patterns are worth stealing — see [plan.md](plan.md#branch-plan) work-item S1 (snapshot-based prewarm) and S2 (branching).

### 5. Templates / Blueprints — Dockerfile-first

- **E2B**: Templates from Dockerfile.
- **Runloop**: Blueprints from Dockerfile (`rli blueprint from-dockerfile`).
- **Modal**: `modal.Image` builder API.
- **Anthropic**: fixed prebaked image (no user templates).

**Common thread:** don't invent your own image DSL; use Dockerfile input and cache Docker layers. Runloop's line is especially clear: "reuse existing Docker layer cache." **Forge alignment:** we already take an `image: str` (any Docker image); users bring their own Dockerfiles. Correct.

### 6. Opaque handle IDs with `expires_at`

- **Anthropic**: `response.container.id` + `response.container.expires_at`; pass ID back to resume.
- **Modal**: `Sandbox.from_id()` / `Sandbox.from_name()` for name-based discovery.
- **E2B**: `Sandbox.connect(sandbox_id)`.
- **Runloop**: `devbox.id` as the sole identity handle.

**Forge status:** we return `workspace.id` but don't advertise `expires_at`. Since MVP has no TTL enforced at the API level, this is fine, but as we add cleanup/retention, exposing `expires_at` becomes important for clients to know when to renew.

### 7. Pause/resume, not stop/destroy

- **E2B**: sandboxes auto-pause on timeout (state preserved, not deleted).
- **Fly**: `suspend` distinct from `stop` — resume-from-suspend is fastest.
- **Runloop**: `suspend` + `resume` are billing-gated states.
- **Anthropic**: checkpoint after ~5min idle, resume on next request.

**Common thread:** deletion is the exception; pausing is the norm. Idle workloads shouldn't cost live compute.

**Forge status:** we destroy workspaces on `delete()` and don't have a pause state. This is a real gap. See [plan.md](plan.md#branch-plan) work-item S3 (pause + resume lifecycle) — but note this only makes sense once V2 gives us microVM snapshots.

### 8. Multi-tenancy via bearer tokens + scoping hierarchy

- **Fly**: `Org → App → Machine` with per-token scoping.
- **Modal**: `Workspace → Environment → App`.
- **E2B**: teams + team-scoped API keys.
- **Daytona**: organization primitive.
- **Anthropic**: `x-api-key` + workspace scoping.

**Forge status:** no auth at all in MVP. This is the #1 pre-production gap. See [plan.md](plan.md#branch-plan) work-item T1 (bearer auth + tenant scoping).

### 9. HTTP-based file API, not just mounts

- **E2B**: `envd` inside VM speaks HTTP for FS/process — uniform API surface across sync/async/browser.
- **Modal**: `sandbox.filesystem.write_text / read_text / copy_from_local`.
- **Daytona**: `sandbox.fs.*` methods on the client.
- **Anthropic**: Files API only; no volume mounts.
- **Forge**: `sandbox.files.*` via HTTP — already correct.

**Common insight:** users want to poke files without spinning up an exec. Forge's file service via HTTP is aligned. Where we differ: our files are on the *host*, not inside the sandbox — the sandbox reads them via a bind mount. That optimization is actually smart (zero-copy) but only works because Docker allows host-side mounts. V2 Firecracker needs an in-guest agent to serve the same file API, à la E2B's `envd`.

### 10. Server tools > client tools where possible

Anthropic's stance is worth internalizing: **when the harness runs the tool, the client's API is declarative.** No round-trip, no client-side execution, secrets stay server-side.

**Forge alignment:** for local self-hosted deployments this pattern is different, but the principle — "let the SDK be declarative; the daemon does the work" — is exactly the shape we have. Reinforcing.

---

## What Forge should steal, ranked

Ordered by impact-per-effort, based on the pattern analysis above.

### Tier 1 — do these before V2 users show up

1. **Bearer-token auth + tenant scoping via `Workspace.metadata`.** ~1 day. Blocks any real deployment. Fly's `Bearer` scheme + Anthropic's workspace scoping are the shape to copy. Track: **T1** in [plan.md](plan.md).

2. **Pause/snapshot as first-class exit path.** ~1 week (needs V2 microVM). E2B's model is the reference: on timeout, snapshot state → resume later, no data loss, no live compute cost. Track: **S3**.

3. **`expires_at` + `container_id`-style opaque handles.** ~half a day. Add `expires_at` to `Workspace` (nullable). Callers know when to renew. Anthropic pattern. Track: **T2**.

4. **Prewarm images at daemon start via config.** ~half a day. `warm_images: ["python:3.14-slim", "node:22-slim"]` in `ForgeConfig`; the pool warms one sub-pool per image on start. Modal's `min_containers` shape. Already possible via manual `pool.start(warm_images=...)` — expose it in config + daemon flags. Track: **T3**.

### Tier 2 — align with industry naming/patterns before publish

5. **Callback-style streaming as an addition to iterator style.** ~half a day. `sandbox.exec(cmd, on_stdout=fn, on_stderr=fn)` for LangChain tools. E2B pattern. Track: **T4**.

6. **`Sandbox.from_id()` / `from_name()` for discovery.** ~half a day. Already have `WorkspaceService.get(id)`; just make it prominent on the top-level `Forge` class. Modal pattern. Track: **T5**.

7. **Sub-namespaced sandbox object.** We already have `ws.files`, `ws.executions`, `ws.snapshots`, `ws.artifacts`. Daytona is the reference. Confirmed correct — no change.

### Tier 3 — larger architectural bets for V2

8. **Snapshot-based fan-out (branching).** ~1 week. Modal's `mount_image()` and Runloop's snapshot-forked devboxes both work this way: snapshot workspace A → boot workspace B from it. Enables "N agents share a starter state" and "checkpoint before risky change." Track: **S1**.

9. **In-guest agent for file API parity in Firecracker.** ~2 weeks. E2B's `envd` is the reference. Runs as PID 1 in the microVM guest, speaks HTTP over vsock, serves the same file operations Forge already exposes over the daemon HTTP API. Track: **F2** in [driver-design.md](driver-design.md).

10. **Tunnel primitive** (agent-hosted service exposed as HTTPS). ~1 week, V2+. Runloop's cleanest primitive. Lets an agent run a web server inside the sandbox that gets a public URL. Track: **T6**, likely V3.

11. **AI Gateway pattern** — credential proxy for LLM providers so agent code never sees raw keys. ~1 week. Runloop's cleanest security feature. Reject in MVP; consider for V3. Track: **out of scope**.

12. **BYOC (bring-your-own-compute) deployment story.** ~2-3 weeks. Both E2B and Daytona pitch this hard. Since Forge is already OSS + self-hostable, this is just documentation + a k8s Helm chart. Track: **T7**, V3.

---

## What Forge should deliberately reject

Every pattern below is popular but wrong for a self-hosted OSS runtime.

### 1. Two-package SDK split (base + code-interpreter)

E2B ships `e2b` and `e2b_code_interpreter` as separate packages, with `run_code` in the latter and `commands.run` in the former. Every developer we talked to (implicitly, via the E2B research doc) found this confusing. **Forge stance:** one package, one primitive (`exec` with a `command` list). If users want a Jupyter-flavored `run_code`, ship it as an optional method on the sandbox that wraps `exec` with a Python kernel — not a separate SDK.

### 2. Blanket no-internet policy

Anthropic's Code Execution has no network egress. That trades away power (no `pip install`, no fetching data) for a simple safety story. **Forge stance:** we want agents that can `pip install`, `git clone`, and hit the OpenAI API. Egress allowlisting is a per-workspace policy knob (V2), not a blanket rule.

### 3. Fixed prebaked image

Same anti-pattern. Anthropic bakes Python 3.11 + a fixed dep list. **Forge stance:** users bring their own Dockerfile-produced image (already the case). V2 Firecracker converts arbitrary OCI images to rootfs.

### 4. `commit()/reload()` explicit consistency (Modal Volume style)

Modal's Volumes require explicit `.commit()` and `.reload()` calls; they're not POSIX. That's a scale trade-off Modal made for their cloud. **Forge stance:** we're a workspace runtime, not a distributed filesystem. Workspaces are POSIX — `os.open()` works exactly as expected. Where we go multi-daemon, we replace the local FS with S3 + local cache (V2), not with eventually-consistent volumes.

### 5. Multi-agent coordination primitives bundled into the runtime

Runloop's `Axon` and `Broker` primitives let agents coordinate — event streams, message passing. **Forge stance:** stay focused. Coordination lives above the workspace runtime. If users want it, they'll layer LangGraph or their own pub/sub on top.

### 6. gVisor as the isolation story

Modal's gVisor is fine for their trust model but Firecracker is measurably better for agent-run untrusted code. **Forge stance:** V2 is Firecracker. See [driver-design.md](driver-design.md).

### 7. `fly-replay`-style app-driven routing

Fly's clever routing headers are irrelevant for local single-tenant AI agent VMs. **Forge stance:** the daemon does the routing; no header protocol on top.

### 8. Hosted-only BYO-Compute (Daytona pattern)

Daytona sells "bring your own AWS/GCP" as a premium tier while keeping the control plane closed. **Forge stance:** we're OSS end-to-end. Anyone can run the whole thing. BYOC is just "run our container in your VPC."

---

## Concrete API shape targets

Based on the pattern analysis, here's the target shape for the V2 SDK. Compare to today's shape:

### Today (Forge MVP)

```python
async with Forge("http://localhost:8787") as forge:
    ws = await forge.workspaces.create(image="python:3.14-slim")
    await ws.files.write("main.py", "print('hi')")
    result = await ws.exec(["python", "main.py"])
    print(result.output, "exit=", result.exit_code)
```

### V2 target (aligned with industry patterns)

```python
async with Forge("http://localhost:8787", api_key=os.environ["FORGE_API_KEY"]) as forge:
    # Create (returns opaque handle)
    ws = await forge.workspaces.create(image="python:3.14-slim", ttl_hours=1)
    # ws.id, ws.expires_at now populated

    # Files — unchanged
    await ws.files.write("main.py", "print('hi')")

    # Exec — buffered (current) or streaming (new callback style)
    result = await ws.exec(["python", "main.py"])
    async for log in ws.exec_stream(["python", "-u", "long_running.py"]):
        print(log.data, end="")

    # OR callback style — LangChain-friendly
    await ws.exec(
        ["python", "main.py"],
        on_stdout=lambda chunk: print(chunk, end=""),
    )

    # Snapshot as identity handle (persistent across daemon restarts)
    snap = await ws.snapshots.create(name="v1")

    # Fan out: N workspaces sharing base state
    forks = await asyncio.gather(*(
        forge.snapshots.fork(snap.id, name=f"branch-{i}")
        for i in range(5)
    ))

    # Pause instead of destroy — resume later without loss
    await ws.pause()
    # ...later...
    resumed = await forge.workspaces.get(ws.id)  # from_id-style discovery
    await resumed.resume()

    # Tunnel — expose an in-workspace service (V3)
    tunnel = await ws.tunnels.create(port=8000)
    print(f"reach the agent's dev server at {tunnel.url}")
```

Every new method above has a corresponding pattern above. None require inventing new mental models — they're all things developers using Modal, E2B, or Runloop already know.

---

## Appendix — per-platform notes

Distilled research findings — retained for reference.

### Modal ([docs](https://modal.com/docs))

- **Primitives:** `modal.App`, `modal.Sandbox`, `modal.Function`, `modal.Image`, `modal.Volume`, `modal.Secret`. Sandbox has `sandbox.filesystem.*` and `sandbox.tunnels`.
- **Hello world:**
  ```python
  app = modal.App.lookup("hello", create_if_missing=True)
  sandbox = modal.Sandbox.create("echo", "hi", app=app)
  print(sandbox.stdout.read())
  sandbox.wait()
  ```
- **Isolation:** gVisor.
- **Filesystem:** `sandbox.filesystem.*` (RPC-style) + `modal.Volume` (mount-style, explicit `.commit()/.reload()`) + `Memory Snapshots` for cold-start.
- **Scaling:** "~1s container boot"; knobs `min_containers`, `buffer_containers`, `scaledown_window`.
- **Auth:** `Workspace → Environment → App` hierarchy; `MODAL_ENVIRONMENT` env.
- **Steal:** durable sandbox + `exec()` pattern; iterable stdout; tag/name discovery; RPC filesystem for ad-hoc access.
- **Reject:** gVisor's shared kernel; Volume's explicit `commit/reload`.

### E2B ([docs](https://e2b.dev/docs))

- **Primitives:** `Sandbox`, `sandbox.commands`, `sandbox.files`, `Template`, `Volume` (beta). Two packages: `e2b` (base) and `e2b_code_interpreter` (Jupyter kernel).
- **Hello world:**
  ```python
  from e2b_code_interpreter import Sandbox
  sbx = Sandbox.create()
  execution = sbx.run_code("print('hello')")
  print(execution.logs)
  ```
- **Isolation:** Firecracker (infra repo Apache-2.0 at [github.com/e2b-dev/infra](https://github.com/e2b-dev/infra)).
- **Filesystem:** Ephemeral by default; pause+snapshot is the default lifecycle. Templates for build-time customization. `envd` daemon inside VM speaks HTTP over vsock.
- **Scaling:** ~4s per GB RAM to pause; ~1s to resume. Snapshot TTL indefinite.
- **Auth:** `E2B_API_KEY`; team-scoped keys; BYOC into AWS/GCP.
- **Steal:** pause/resume as default lifecycle; snapshot-based fast resume; `envd`-style in-VM HTTP daemon; Templates registry with Dockerfile input.
- **Reject:** two-package SDK split; no-context-manager auto-close.

### Daytona ([docs](https://www.daytona.io/docs))

- **Primitives:** `Daytona`, `AsyncDaytona`, `Sandbox` with `sandbox.process`, `sandbox.fs`, `sandbox.git`, `sandbox.lsp_server`, `sandbox.object_storage`. `Snapshot`, `Volume`, `Secret`, `Image` modules.
- **Hello world:**
  ```python
  from daytona import Daytona, DaytonaConfig
  daytona = Daytona(DaytonaConfig(api_key=KEY))
  sandbox = daytona.create()
  print(sandbox.process.code_run("print('hi')").result)
  ```
- **Isolation:** Docs coy; sub-90ms creation suggests microVM.
- **Filesystem:** Stateful by design; snapshots + volumes + git first-class.
- **Scaling:** "Sub-90ms sandbox creation" advertised. Snapshots-as-templates.
- **Auth:** API keys, organizations, BYO-Compute tier.
- **Steal:** sub-namespaced sandbox object; snapshot-as-template; sync + async parity.
- **Reject:** sprawling one-SDK surface (LSP/ObjectStorage/ComputerUse); coyness about isolation.

### Runloop ([docs](https://docs.runloop.ai))

- **Primitives:** `Devbox`, `Blueprint` (Dockerfile → image), `Snapshot`, `Tunnel`, `Secret`, `AI Gateway`, `Axon` (event stream), `Broker`, `Agent`.
- **Hello world:**
  ```python
  runloop = AsyncRunloopSDK()
  devbox = await runloop.devbox.create()
  result = await devbox.cmd.exec(command="echo hi")
  print(await result.stdout())
  await devbox.shutdown()
  ```
- **Isolation:** microVM; VM + container in-guest. Hypervisor unnamed.
- **Filesystem:** Persistent per-devbox disk; disk-only snapshots. 16 GiB default.
- **Scaling:** "10GB image startup <2s". Blueprints leverage Docker layer cache.
- **Auth:** `RUNLOOP_API_KEY`; Bearer; no explicit tenancy in docs.
- **Steal:** Dockerfile→Blueprint pipeline; disk-snapshot-for-fan-out; Tunnel primitive.
- **Reject:** bundling multi-agent coordination (Axon/Broker) into the runtime.

### Fly.io Machines ([docs](https://fly.io/docs/machines/))

- **Primitives:** `App`, `Machine`, `Volume`, `Token`. Nested `Org → App → Machine`.
- **Hello world (REST):**
  ```
  curl -X POST "$FLY_API_HOSTNAME/v1/apps/{app}/machines" \
    -H "Authorization: Bearer $FLY_API_TOKEN" \
    -d '{"config": {"image": "nginx:latest"}}'
  ```
- **Isolation:** Firecracker.
- **Filesystem:** Local NVMe volumes per Machine; on-demand + scheduled snapshots.
- **Scaling:** "Subsecond" resume from suspended; `min_machines_running` prewarm.
- **Auth:** Bearer tokens (`fly tokens deploy`); Org → App → Machine scoping.
- **Steal:** App → Machine → Volume nesting; suspend vs stop as separate states; small REST + OpenAPI (no forced SDK).
- **Reject:** local-only NVMe volumes; app-driven routing.

### Anthropic Code Execution ([docs](https://docs.claude.com/en/docs/agents-and-tools/tool-use/code-execution-tool))

- **Primitive:** implicit `container` addressed by ID; server-side tool.
- **Hello world:**
  ```python
  client.messages.create(
      model="claude-opus-4-8", max_tokens=4096,
      messages=[{"role":"user","content":"compute mean of [1,2,3]"}],
      tools=[{"type":"code_execution_20250825","name":"code_execution"}],
  )
  ```
- **Isolation:** "Sandboxed container" — details opaque. No internet.
- **Filesystem:** Files API for uploads/downloads (`container_upload` blocks). Persists between turns in same container.
- **Scaling:** Container reused across turns; checkpointed after ~5min idle; 30-day expiration.
- **Auth:** `x-api-key`; workspace scoping.
- **Steal:** opaque `container_id` handles + `expires_at`; server-tool ergonomics; single Files-API-style ingress/egress.
- **Reject:** blanket no-internet policy; fixed prebaked image.

### LangSmith Sandbox (public-info only)

Deep-Agents ships `LangSmithSandbox` as a backend. Public docs are thin; based on the code path in `deepagents.backends.langsmith`:

- **Primitive:** Uses LangSmith's tracing platform as the execution host.
- **Steal:** the pattern of exposing the sandbox through an existing developer-tools platform (LangSmith accounts are common in the LangChain ecosystem).
- **Not applicable to Forge:** we don't ship a tracing platform; users can plug LangSmith in above Forge if they want traces.

---

## Related reading

- [plan.md](plan.md) — the V2 branch plan informed by this analysis.
- [driver-design.md](driver-design.md) — Firecracker driver specifics.
- [../architecture/overview.md](../architecture/overview.md) — Forge as it stands today.
- [../architecture/pool-and-runtime-session.md](../architecture/pool-and-runtime-session.md) — the pool abstraction we're going to slot Firecracker into.
