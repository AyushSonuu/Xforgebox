# Architecture docs

Deep dives on how Forge is built. Start with [overview.md](overview.md) and branch out based on what you need to know.

## Reading order

**If you're new to the project, read in this order:**

1. **[overview.md](overview.md)** — the end-to-end picture. System context, package layout, layer contracts, the critical "one execute() call" sequence diagram, and every domain object. About 15 minutes.
2. **[pool-and-runtime-session.md](pool-and-runtime-session.md)** — the load-bearing abstraction. Why Forge exists as a distinct project reduces to this doc. Acquire mechanics, race conditions, reaper, session bursts, and where the abstraction bends for V2.
3. **[data-model.md](data-model.md)** — every entity, its storage location, on-disk layout, metastore schema, and serialization boundaries.

## What's in each doc, at a glance

| Doc | Answers |
|---|---|
| [overview.md](overview.md) | "What are the pieces and how do they fit?" — system context, package layout, class diagram, ER diagram, the critical exec path sequence |
| [pool-and-runtime-session.md](pool-and-runtime-session.md) | "How does one exec really work end-to-end and what stops the pool from over-provisioning?" — concurrency invariants, the `RuntimeSession` protocol, state machine, reaper |
| [data-model.md](data-model.md) | "Where is every field stored and what invariants hold?" — SQL DDL, serialization boundaries, trust boundaries in the model |

## Where the diagrams are

Every doc uses mermaid. GitHub renders these natively. Types used:

- **`graph LR/TD`** for system context and package layout.
- **`classDiagram`** for domain model + protocol relationships.
- **`sequenceDiagram`** for critical paths (one exec, one acquire).
- **`stateDiagram-v2`** for lifecycles (container state, pool state, execution status transitions).
- **`erDiagram`** for the SQLite schema.
- **`flowchart`** for reaper + integration flows.

If you're editing these locally and want a preview, use VS Code's built-in mermaid preview or the online editor at [mermaid.live](https://mermaid.live).

## What's *not* here

- **User-facing quickstart / SDK reference** — that's [../../README.md](../../README.md) and the notebook at [../../examples/notebooks/deep_agents_quickstart.ipynb](../../examples/notebooks/deep_agents_quickstart.ipynb).
- **Roadmap and V2/V3 direction** — [../v2/plan.md](../v2/plan.md), [../v2-design.md](../v2-design.md), [../v3-design.md](../v3-design.md).
- **Deployment recipes** — coming as a V2 branch (`deploy/`).
- **Original MVP design doc + amendments** — [../mvp-design.md](../mvp-design.md), [../mvp-implementation-notes.md](../mvp-implementation-notes.md).
