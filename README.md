# Forge

**A resource-pooled workspace runtime for AI agents.** Persistent workspaces, isolated command execution, snapshots, artifacts, and a real LangChain / Deep-Agents backend — with a shared Docker container pool that lets many agents share a few warm containers instead of one-per-agent.

## Why

Deep-agent frameworks give each conversation its own sandbox. In practice, ~90% of agent time is spent thinking (LLM calls); only ~10% is exec. Running one warm container per agent burns RAM 10× over. Forge decouples the two:

- **Workspaces** are per-agent, persistent host directories — cheap.
- **Containers** are a shared, fungible pool — expensive, but reused across agents.
- **`pool.session(workspace_id, image)`** binds a workspace to a pooled container just for the burst of tool calls that need it, then releases.

Every command runs with `cwd=/workspace` regardless of runtime, so migrating from Docker (MVP) to Firecracker (V2) or K8s (V3) doesn't touch agent code.

## Install

```bash
uv sync --all-extras --dev
docker pull python:3.14-slim   # or set FORGE_TEST_IMAGE=your-image
```

Requires Python 3.14 and a running Docker daemon.

## Quickstart — daemon + client

Start the daemon:

```bash
uv run forged serve --host 127.0.0.1 --port 8787 --pool-min-idle 2 --pool-max 8
```

From another shell:

```bash
uv run forge workspace create --image python:3.14-slim
# → ws_abc123...

uv run forge files write ws_abc123 main.py <(echo "print('hi from forge')")
uv run forge exec ws_abc123 -- python main.py
# hi from forge

uv run forge pool status
# python:3.14-slim  idle=2  in_use=0  total=2  max=8  leases=1  kills=0
```

## Quickstart — Python SDK

```python
import asyncio
from forge.client import Forge

async def main():
    async with Forge("http://127.0.0.1:8787") as forge:
        ws = await forge.workspaces.create(image="python:3.14-slim")
        await ws.files.write("main.py", "print('hi')\n")
        result = await ws.exec(["python", "main.py"])
        print(result.output, "exit=", result.exit_code)
        # Snapshot -> restore into a new workspace:
        snap = await ws.snapshots.create(name="v1")

asyncio.run(main())
```

For embedded use (no daemon), swap `Forge("http://...")` for `Forge.local(config=...)`.

## Deep-Agents integration

Forge implements `deepagents.backends.BaseSandbox` — pass it straight to `create_deep_agent`:

```python
from deepagents import create_deep_agent
from forge.client import Forge
from forge.langchain import ForgeSandbox

async with Forge("http://127.0.0.1:8787") as forge:
    backend = await ForgeSandbox.afrom_thread(
        forge=forge, thread_id="thread-42", image="python:3.14-slim"
    )
    agent = create_deep_agent(model="anthropic:claude-sonnet-4", backend=backend)
    # ...invoke as usual
```

`BaseSandbox` provides default `ls` / `read` / `write` / `edit` / `glob` / `grep` on top of `execute()`, which Forge implements natively async. Command failures never raise — Deep-Agents inspects `exit_code`.

## Resource-sharing demo

```
$ uv run python examples/concurrent_agents_demo.py
[demo] launching 20 agents against pool max_size=4, 5 execs each...
[demo] 20 agents / 100 execs / wall=2.23s / max concurrent containers=4 (bound=4) / p50=379.0ms / p95=731.0ms / total_leases=100
```

20 concurrent agents, each doing 5 execs, share 4 warm containers with p95 latency under a second.

## Architecture

```
Agent process(es) — Deep-Agents / LangGraph / custom
        │  ForgeSandbox (deepagents BaseSandbox subclass)
        v
   Forge SDK (HTTP or in-process)
        │
        v
   forged (FastAPI daemon)
   ├── Workspace/Files/Execution/Snapshot/Artifact services
   ├── ContainerPool  ←── the shared resource
   ├── DockerDriver (aiodocker)
   ├── MetaStore (SQLite)  ·  EventBus (in-mem → SSE)
   └── Host FS: /var/lib/forge/{workspaces, snapshots, artifacts}
```

Every command runs `forge-run <user-cmd>` inside a pooled container; `forge-run` `cd`s into the workspace bound by the pool session, so user code always sees `cwd=/workspace`. See [docs/mvp-implementation-notes.md](docs/mvp-implementation-notes.md) for the `RuntimeSession` design amendment (A1) that keeps the API stable for V2 Firecracker.

## Docs

- [Product definition](docs/product.md)
- [MVP design](docs/mvp-design.md)
- [MVP implementation notes](docs/mvp-implementation-notes.md) — design amendments + branch progress
- [V2 direction](docs/v2-design.md) · [V3 direction](docs/v3-design.md)
- [Architecture review](docs/review.md)

## Test status

- 127 tests pass (unit + integration).
- Integration suite spins real Docker containers on `python:3.14-slim`.
- ruff + mypy strict clean.

## Roadmap

- **V2:** Firecracker driver (production isolation), S3 workspace store, secret + network policy.
- **V3:** Distributed workers, scheduler, K8s driver, observability + policy engine.

The `RuntimeSession` contract is designed so both drop in without touching the SDK or Deep-Agents integration.
