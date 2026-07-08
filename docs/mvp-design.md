# MVP Design Document

## MVP Objective

The MVP should prove the core product claim:

> An AI agent can use Forge to create a persistent workspace, run commands inside it, keep state, snapshot it, and restore it later.

The MVP should be intentionally small and should not attempt to solve every future runtime, storage, or plugin concern.

## MVP Architecture

```text
Client SDK / CLI
      |
      v
Forge API Layer
      |
      v
Workspace Service
      |
      +-------------------+
      |                   |
      v                   v
Workspace Store       Runtime Driver
Local Filesystem      Docker
      |                   |
      v                   v
Snapshots            Executions
Archive Store        stdout/stderr/exit code
```

## MVP Core Types

The MVP should be implemented in Python and expose Python-native models. The examples below use `dataclasses` and `typing` to keep the contract explicit without requiring a heavy framework.

### Workspace

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Workspace:
    id: str
    spec: "WorkspaceSpec"
    status: "WorkspaceStatus"
    created_at: datetime
    updated_at: datetime
    name: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
```

### WorkspaceSpec

```python
@dataclass(frozen=True)
class ResourceLimits:
    cpu: float | None = None
    memory: str | None = None
    disk: str | None = None


@dataclass(frozen=True)
class WorkspaceSpec:
    image: str
    runtime: Literal["docker"] = "docker"
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
```

### Environment

```python
@dataclass(frozen=True)
class Environment:
    id: str
    workspace_id: str
    runtime: str
    status: Literal["creating", "running", "stopped", "failed"]
```

### Execution

```python
@dataclass(frozen=True)
class Execution:
    id: str
    workspace_id: str
    environment_id: str
    command: list[str]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

### Snapshot

```python
@dataclass(frozen=True)
class Snapshot:
    id: str
    workspace_id: str
    created_at: datetime
    format: Literal["tar.zst"]
    size_bytes: int
    parent_id: str | None = None
```

### Artifact

```python
@dataclass(frozen=True)
class Artifact:
    id: str
    workspace_id: str
    path: str
    size_bytes: int
    created_at: datetime
    content_type: str | None = None
```

## MVP Interfaces

The MVP interfaces should be Python protocols so implementations can remain swappable without forcing inheritance.

### RuntimeDriver

```python
from collections.abc import AsyncIterator
from typing import Protocol


class RuntimeDriver(Protocol):
    @property
    def name(self) -> str:
        ...

    def capabilities(self) -> "RuntimeCapabilities":
        ...

    async def create_environment(
        self, request: "CreateEnvironmentRequest"
    ) -> "EnvironmentHandle":
        ...

    async def destroy_environment(self, environment_id: str) -> None:
        ...

    async def exec(self, request: "ExecRequest") -> "ExecutionHandle":
        ...

    async def stop_execution(self, execution_id: str) -> None:
        ...

    def stream_logs(self, execution_id: str) -> AsyncIterator["LogEvent"]:
        ...
```

### WorkspaceStore

```python
from pathlib import Path
from typing import Protocol


class WorkspaceStore(Protocol):
    async def create(self, request: "CreateWorkspaceRequest") -> Workspace:
        ...

    async def get(self, workspace_id: str) -> Workspace:
        ...

    async def list(self) -> list[Workspace]:
        ...

    async def delete(self, workspace_id: str) -> None:
        ...

    async def path(self, workspace_id: str) -> Path:
        ...

    async def snapshot(self, workspace_id: str) -> Snapshot:
        ...

    async def restore(self, snapshot_id: str) -> Workspace:
        ...
```

### ArtifactStore

```python
from collections.abc import AsyncIterator
from typing import Protocol


class ArtifactStore(Protocol):
    async def export_file(self, workspace_id: str, path: str) -> Artifact:
        ...

    async def get(self, artifact_id: str) -> Artifact:
        ...

    def read(self, artifact_id: str) -> AsyncIterator[bytes]:
        ...
```

### EventBus

```python
from collections.abc import AsyncIterator
from typing import Protocol


class EventBus(Protocol):
    async def publish(self, event: "ForgeEvent") -> None:
        ...

    def subscribe(self, event_filter: "EventFilter") -> AsyncIterator["ForgeEvent"]:
        ...
```

## MVP User Flows

### Create Workspace

```text
User/Agent -> Forge API -> WorkspaceStore creates local directory -> RuntimeDriver prepares Docker container -> Workspace returned
```

### Execute Command

```text
User/Agent -> Forge API -> RuntimeDriver exec -> logs streamed -> exit code returned -> workspace files remain on disk
```

### Snapshot Workspace

```text
User/Agent -> Forge API -> WorkspaceStore archives workspace directory -> Snapshot metadata returned
```

### Restore Workspace

```text
User/Agent -> Forge API -> WorkspaceStore extracts snapshot into a new workspace directory -> Workspace returned
```

## MVP CLI Examples

```bash
forge workspace create --name demo --image python:3.13
forge exec demo -- python -c "print('hello from forge')"
forge files write demo main.py ./main.py
forge exec demo -- python main.py
forge snapshot create demo --name after-main
forge snapshot restore after-main --name demo-restored
forge artifact export demo ./report.json
```

## MVP SDK Example

```python
workspace = await forge.workspaces.create(
    name="demo",
    image="python:3.13",
    runtime="docker",
)

await workspace.files.write("main.py", "print('hello from forge')")

execution = await workspace.exec(["python", "main.py"])

async for log in execution.logs():
    print(log.message, end="")

await workspace.snapshot(name="after-main")
```

## MVP Security Model

The MVP should clearly state that Docker provides process/container-level isolation and is suitable for local development or trusted workloads, not strong hostile multi-tenant isolation by itself.

MVP controls should include:

- Execution timeout.
- Memory limit where supported.
- CPU limit where supported.
- Optional network disablement where supported.
- Workspace directory isolation.
- No host Docker socket mounted into environments.

## MVP Risks

### Risk: Overbuilding the core

Mitigation: keep interfaces internal and narrow until Docker and Firecracker both validate them.

### Risk: Users expect Firecracker immediately

Mitigation: document Docker as the MVP runtime and Firecracker as the V2 production-isolation path.

### Risk: Snapshot performance is poor for large workspaces

Mitigation: archive snapshots are acceptable for MVP; incremental snapshots belong in V2 or V3.

### Risk: Security is oversold

Mitigation: document runtime-specific security boundaries.
