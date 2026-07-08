# High-level Design

## Objective

Forge should be a Python-first workspace runtime that can be used directly by AI agent frameworks. For V1, the most important integration target is LangChain Deep Agents sandboxes so developers can pass a Forge-backed sandbox backend directly to `create_deep_agent(...)`.

## V1 Compatibility Goal

V1 should ship a `ForgeSandbox` adapter that implements the Deep Agents sandbox backend contract. LangChain's Deep Agents sandbox model treats sandboxes as backends that expose standard filesystem tools plus an `execute` tool. Forge should match that model so an agent can use Forge without custom glue code.

## External Integration Surface

```text
LangChain / Deep Agents
        |
        v
ForgeSandbox adapter
        |
        v
Forge Python SDK
        |
        v
Forge API service
        |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
Workspace Store       Runtime Driver       Artifact Store
Local / future S3     Docker / future VM   Local / future object store
```

## V1 Runtime Pattern

V1 should prioritize the "sandbox as tool" pattern:

1. The LangChain agent runs in the user's app process.
2. The agent receives filesystem tools and an `execute` tool through the backend.
3. The backend forwards file and command operations to Forge.
4. Forge runs commands inside the isolated workspace runtime.
5. Secrets and agent state remain outside the sandbox unless explicitly passed.

This pattern keeps agent code easy to update and reduces the need to rebuild runtime images for every agent change.

## V1 Scope

V1 should include:

- Python package for the Forge SDK.
- `ForgeSandbox` Deep Agents backend adapter.
- Local Forge API or in-process client mode.
- Docker-backed isolated execution.
- Workspace lifecycle mapping to sandbox lifecycle.
- `execute(command: str)` support.
- Filesystem operations required by Deep Agents backends.
- File transfer methods for seeding workspaces and retrieving artifacts.
- Thread-scoped and assistant-scoped workspace mapping helpers.
- TTL cleanup support.

## Scoping Model

Forge should support two first-class scoping modes that map to agent usage:

### Thread-scoped Workspace

Each conversation thread receives its own Forge workspace. Follow-up turns reuse the same workspace until TTL expiry or explicit deletion.

### Assistant-scoped Workspace

All threads for the same assistant share a workspace. This enables persistent packages, repositories, generated files, and longer-lived project state, but requires cleanup policies and quotas.

## Security Model

Forge should keep secrets outside the sandbox by default. If an agent needs authenticated access, prefer host-side tools or an outbound proxy that attaches credentials outside the sandbox boundary. Injecting API keys directly into the workspace should be treated as unsafe for autonomous agents.

## Latency Model

LangChain-style sandbox calls may execute many small operations. Forge should reduce latency through:

- persistent per-thread workspaces,
- warm Docker containers or warm runtime pools,
- batched file operations,
- bounded command output,
- nearby workspace storage,
- cache volumes for dependencies.

## HLD Decision

V1 should not only expose a generic Forge SDK. It should also provide a concrete LangChain-compatible sandbox adapter so developers can use Forge directly with Deep Agents.
