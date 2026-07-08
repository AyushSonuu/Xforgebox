# V2 Design Direction

## V2 Objective

V2 should move Forge from a local developer tool toward a production-capable workspace runtime with stronger isolation, remote persistence, and runtime capability negotiation.

## Major Additions

### Firecracker Runtime Driver

The Firecracker driver should provide microVM-backed execution for workloads that require stronger isolation than Docker.

Responsibilities:

- Prepare microVM configuration.
- Attach root filesystem and workspace storage.
- Configure CPU and memory.
- Configure network isolation.
- Start, stop, and destroy microVMs.
- Execute commands through a guest agent or init protocol.
- Stream stdout and stderr.
- Report runtime capabilities.

### Runtime Capabilities

Runtimes should report concrete capabilities instead of being checked by name.

Example:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RuntimeCapabilities:
    isolation: Literal["process", "container", "microvm", "vm", "wasm"]
    snapshots: bool
    pause_resume: bool
    network_control: bool
    resource_limits: bool
    hot_attach_volume: bool
    gpu: bool
```

### Remote Workspace Store

Add an S3-compatible workspace store for persistence beyond one machine.

Responsibilities:

- Store workspace revisions.
- Store snapshots.
- Restore workspace state onto a worker.
- Support large artifacts.
- Support eventual incremental sync.

### Secret Handling

Secrets should be injected explicitly and should not be stored in workspace snapshots by default.

Principles:

- Secret references live in specs.
- Secret values are resolved just-in-time.
- Secret values are scoped to an execution or environment.
- Secret access is audited.

### Network Policy

V2 should support basic network controls:

- Disabled network.
- Outbound internet allowed.
- Domain or CIDR allowlist where feasible.
- Internal service access where configured.

### Cache Volumes

Dependency caches should be separate from workspace state.

Examples:

- Python package cache.
- Node package cache.
- Model cache.
- Build cache.

This avoids polluting snapshots and improves repeated execution speed.

## V2 Architecture

```text
Client SDK / CLI
      |
      v
Forge API Service
      |
      +--------------------+--------------------+
      |                    |                    |
      v                    v                    v
Workspace Store       Runtime Drivers       Secret Provider
Local / S3            Docker / Firecracker  Local / External
      |                    |
      v                    v
Snapshot Backends     Capability Registry
```

## V2 Design Questions

- Should Firecracker execution use a guest agent, SSH, vsock, or init-command handoff?
- Should OCI images be converted into rootfs images directly or through a builder pipeline?
- How should workspace mounts be exposed to Firecracker: block device, virtio-fs, or copied rootfs?
- What is the minimum hardening profile required before marketing Firecracker as production isolation?
- Should S3 storage be revision-based, snapshot-based, or both?

## V2 Exit Criteria

V2 is complete when:

- Docker and Firecracker both implement the same runtime interface.
- The Python SDK does not change when switching runtimes.
- Workspace state can persist to S3-compatible storage.
- Runtime security boundaries are documented.
- Basic secrets, network controls, and resource controls exist.
