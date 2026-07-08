# V3 Design Direction

## V3 Objective

V3 should make Forge a distributed workspace platform that can run across multiple machines, enforce policy, and support multiple runtime and storage backends in production.

## Major Additions

### Remote Workers

Workers run environments and report capacity to the control plane.

Worker responsibilities:

- Register with the control plane.
- Advertise runtime capabilities.
- Advertise available CPU, memory, disk, and optional GPU resources.
- Pull or restore workspace state.
- Run environments and executions.
- Stream logs and events.
- Clean up idle environments.

### Scheduler

The scheduler chooses where a workspace or execution should run.

Scheduling inputs:

- Runtime requirement.
- Capability requirement.
- CPU and memory needs.
- Workspace data location.
- Cache locality.
- Tenant policy.
- Worker health.
- Cost or priority.
- Expected startup latency and queue wait time.

### Kubernetes Runtime Driver

The Kubernetes driver should allow Forge to use existing clusters as execution capacity.

Responsibilities:

- Create pods/jobs for environments.
- Mount workspace storage.
- Apply resource limits.
- Stream logs.
- Clean up workloads.
- Integrate with cluster autoscaling where available.
- Report Kubernetes-specific capabilities.

### Autoscaling

Forge should scale workers horizontally based on queue depth, active executions, CPU and memory pressure, workspace restore time, cache hit rate, and latency service-level objectives. Autoscaling should support both warm pools for low-latency startup and cold expansion for cost efficiency.

### Latency Management

Latency should be measured across the whole execution path: API admission, scheduling, workspace restore, image preparation, environment startup, command startup, log streaming, and artifact export. The scheduler should prefer warm workers, nearby workspace data, hot caches, and runtime pools when latency matters.

### Policy Engine

Policies should govern:

- Allowed images.
- Allowed runtimes.
- Network access.
- Secret access.
- Maximum CPU and memory.
- Maximum execution duration.
- Artifact export permissions.
- Snapshot retention.

### Observability

V3 should provide production observability:

- Structured events.
- Metrics.
- Distributed traces.
- Audit logs.
- Execution timelines.
- Workspace lifecycle history.

### Extension Interfaces

Only after Docker, Firecracker, local storage, S3 storage, and Kubernetes validate the internal interfaces should Forge expose a stable extension API.

Candidate extension types:

- Runtime drivers.
- Workspace stores.
- Snapshot backends.
- Secret providers.
- Policy providers.
- Artifact stores.

## V3 Architecture

```text
                 Client SDK / REST API
                         |
                         v
                  Forge Control Plane
                         |
     +-------------------+-------------------+
     |                   |                   |
     v                   v                   v
 Scheduler          Policy Engine       Metadata Store
     |
     v
 Remote Workers
     |
     +---------------+----------------+----------------+
     |               |                |                |
     v               v                v                v
 Docker Driver   Firecracker Driver   Kubernetes      Future Drivers
```

## V3 Exit Criteria

V3 is complete when:

- Multiple workers can run workspaces.
- Scheduling decisions consider runtime capabilities and resource availability.
- Operators can enforce policy centrally.
- Observability is sufficient for production debugging, capacity planning, and latency tuning.
- Extension APIs are stable enough for third-party implementations.
