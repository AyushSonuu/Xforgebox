# V2 planning

The Firecracker-based next step for Forge, informed by six months of research into how Modal, E2B, Daytona, Runloop, Fly.io, Anthropic Code Execution, and LangSmith Sandbox ship their SDKs.

## Read in this order

1. **[plan.md](plan.md)** — the branch plan. Three tracks (F: Firecracker, T: tenancy/ops, S: snapshots) plus quality-of-life items (Q). Ordered, dependency-annotated, with V2 exit criteria and explicit non-goals.
2. **[driver-design.md](driver-design.md)** — the technical companion for Track F. Firecracker mechanics: guest agent, image cache, boot path, workspace volumes, snapshots, networking, testing strategy.
3. **[sdk-parity.md](sdk-parity.md)** — the SDK research that informed everything else. Comparison table of the seven platforms, consistent patterns Forge should align with, and patterns Forge should deliberately reject.

## The one-liner rationale

> Forge V2 turns a working single-tenant local runtime into a production-ready sandbox by swapping Docker for Firecracker (real isolation), adding auth + quotas + retention (real operability), and aligning the SDK with industry patterns (real user familiarity). The V1 `RuntimeSession` contract survives unchanged.

## What's decided vs open

**Decided** (in this planning folder):

- The V2 branch plan is 25-30 branches in three tracks (F/T/S) plus Q items — see [plan.md § branch plan](plan.md#branch-plan).
- Firecracker is the isolation choice, not gVisor / Kata / plain namespaces. Rationale in [sdk-parity.md § Modal (reject: gVisor)](sdk-parity.md#appendix--per-platform-notes) and [driver-design.md](driver-design.md).
- `forge-guest` is the in-VM daemon, written in Go, distributed as a static binary baked into the wheel. See [driver-design.md § guest agent](driver-design.md#guest-agent-forge-guest).
- Per-workspace ext4 volumes will eventually replace the shared bind mount, adopting E2B's "one workspace = one microVM" model. Staged as F5 — [plan.md](plan.md#branch-plan).
- Pause/resume becomes the default idle-timeout behavior, not destroy. E2B pattern. Staged as S2.
- Auth is bearer-token first (Fly.io / Anthropic pattern), with OIDC via reverse proxy documented as the standard integration path. Staged as T1.
- Non-goals explicitly listed: distributed workers, K8s driver, GPU scheduling, WASM, agent-framework primitives. All V3 or out-of-scope. See [plan.md § non-goals](plan.md#non-goals-for-v2).

**Open** (deliberately, until implementation reveals answers):

- Rust vs Go for `forge-guest` — leaning Go for MVP.
- Firecracker vs Cloud Hypervisor — leaning Firecracker for MVP.
- Snapshot on idle at driver level vs pool level — leaning driver.
- Whether S1/S2 requires F6 or can ship on Docker with a partial semantic (fork works on Docker via snapshot-restore; pause is a no-op).

## Not here

- V3 direction (distributed workers, K8s, GPU) → [../v3-design.md](../v3-design.md).
- The original MVP shape → [../mvp-design.md](../mvp-design.md).
- V1 as-built → [../architecture/](../architecture/).
- Deployment recipes → will land as `../deployment/` when T8 branches.
