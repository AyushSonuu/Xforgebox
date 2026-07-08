# Forge documentation

**Everything about how Forge is built, how it fits together today, and where it's going.** If you're new here, start with the top-level [README](../README.md) — this folder is for people who want to understand or extend the codebase.

## Where to start

| I want to... | Read this |
|---|---|
| Run agents against Forge for the first time | [../README.md](../README.md) + [../examples/notebooks/deep_agents_quickstart.ipynb](../examples/notebooks/deep_agents_quickstart.ipynb) |
| Understand the architecture end-to-end | [architecture/overview.md](architecture/overview.md) |
| Understand the container pool + `RuntimeSession` contract | [architecture/pool-and-runtime-session.md](architecture/pool-and-runtime-session.md) |
| Understand the data model + on-disk layout + metastore schema | [architecture/data-model.md](architecture/data-model.md) |
| Understand the original MVP shape | [mvp-design.md](mvp-design.md) |
| Read the design amendments made during implementation | [mvp-implementation-notes.md](mvp-implementation-notes.md) |
| Understand the LangChain / Deep-Agents mapping | [low-level-design.md](low-level-design.md) + [high-level-design.md](high-level-design.md) |
| Plan V2 (Firecracker + auth + snapshots) | [v2/README.md](v2/README.md) |
| See how Forge compares to Modal / E2B / Daytona / Runloop / Fly / Anthropic | [v2/sdk-parity.md](v2/sdk-parity.md) |
| Understand where V3 is heading | [v3-design.md](v3-design.md) |
| Read the product framing | [product.md](product.md) |
| Read the architecture review of the original design | [review.md](review.md) |
| Read the (superseded but historical) V2 sketch | [v2-design.md](v2-design.md) |
| Read the MVP feature plan | [mvp-implementation-plan.md](mvp-implementation-plan.md) |
| Read the roadmap | [roadmap.md](roadmap.md) |

## Folder tour

```
docs/
├── README.md                       ← you are here
│
├── architecture/                   ← how Forge works today
│   ├── README.md
│   ├── overview.md                 ← the top-level architecture doc
│   ├── pool-and-runtime-session.md ← the pool + session abstraction
│   └── data-model.md               ← every entity, schema, on-disk layout
│
├── v2/                             ← where Forge is going next
│   ├── README.md
│   ├── plan.md                     ← V2 branch plan (Firecracker + tenancy + snapshots)
│   ├── driver-design.md            ← Firecracker driver technical design
│   └── sdk-parity.md               ← comparison with Modal / E2B / Daytona / Runloop / Fly / Anthropic / LangSmith
│
├── mvp-design.md                   ← original MVP shape + core types
├── mvp-implementation-notes.md     ← amendments captured during MVP build (A1: RuntimeSession, A2: Python 3.14, A3: BaseSandbox subclass)
├── mvp-implementation-plan.md      ← alternative feature-level plan for MVP (pre-implementation)
├── high-level-design.md            ← LangChain integration HLD
├── low-level-design.md             ← LangChain adapter method mapping
├── product.md                      ← product framing
├── review.md                       ← architecture review notes
├── roadmap.md                      ← rough roadmap
├── v2-design.md                    ← original V2 sketch (superseded by v2/plan.md)
└── v3-design.md                    ← V3 direction (distributed workers, K8s, policy engine)
```

## Diagram conventions

All architecture docs use mermaid. GitHub renders these natively. Types you'll see:

- **`graph LR/TD`** — system context, package layout.
- **`classDiagram`** — domain model + protocol relationships.
- **`sequenceDiagram`** — critical paths (one exec, one acquire).
- **`stateDiagram-v2`** — lifecycles (container state, execution status).
- **`erDiagram`** — SQLite schema.
- **`flowchart`** — reaper, integration flows.

To preview locally, use VS Code's built-in mermaid preview, [mermaid.live](https://mermaid.live), or `npx @mermaid-js/mermaid-cli`.

## Contributing to the docs

- If you change a public API, update the corresponding architecture doc in the same PR.
- If you swap in a new backend (e.g. Postgres metastore, S3 artifact store), add a new section to [architecture/data-model.md](architecture/data-model.md); do not delete the old one — someone still runs it.
- Amendments to the design (things that were true at merge-time but proved wrong later) go into [mvp-implementation-notes.md](mvp-implementation-notes.md) as new sections. Don't rewrite history.
- Mermaid diagrams: keep them small (≤20 nodes). If a diagram is getting sprawly, it's usually a sign the architecture itself needs cleanup.
