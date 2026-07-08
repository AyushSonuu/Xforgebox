# Forge Roadmap

## Roadmap Philosophy

Forge should grow from a small useful runtime into a pluggable workspace platform. The project should avoid freezing broad abstractions until at least two real implementations validate each major interface.

The recommended sequence is:

1. Make the simple local developer experience excellent.
2. Add production-grade isolation.
3. Add distributed scale.
4. Stabilize extension APIs only after real backend diversity exists.

## Phase 0: Product Foundation

Status: documentation and architecture definition.

Goals:

- Define product scope.
- Define MVP boundaries.
- Define core model, terminology, and Python-first implementation direction.
- Avoid premature platform complexity.

Deliverables:

- Product definition.
- MVP design document.
- V2 design direction.
- V3 design direction.
- Architecture review.

## Phase 1: MVP — Local Persistent Workspaces

Primary goal: prove that the workspace abstraction is useful before investing in microVM complexity.

### Required Capabilities

- Local workspace store.
- Docker runtime driver.
- Create/list/get/delete workspace APIs.
- Execute commands inside a workspace.
- Stream stdout and stderr.
- Return exit code and execution status.
- Write/read files through SDK or CLI.
- Snapshot and restore using archive-based snapshots.
- Basic artifact export.
- Python SDK as the first-class SDK.
- CLI for core workflows.

### Out of Scope

- Firecracker.
- Multi-node scheduling.
- External plugin SDK.
- GPU support.
- Browser IDE.
- Complex package-manager abstraction.
- Universal image builder.
- Strong multi-tenant security claims.

### MVP Exit Criteria

- A developer can run Forge locally and execute agent-generated code in a persistent workspace.
- State persists between commands.
- A workspace can be snapshotted and restored.
- The runtime driver boundary is clean enough to start a second runtime implementation.

## Phase 2: V2 — Secure Runtime and Remote Storage

Primary goal: make Forge suitable for controlled production environments.

### Required Capabilities

- Firecracker runtime driver.
- Runtime capability reporting.
- OCI-to-rootfs or equivalent image preparation path for Firecracker.
- Network controls per environment.
- Resource limits for CPU, memory, disk, and execution time.
- S3-compatible workspace store.
- Cache volumes for dependencies and build artifacts.
- Secret injection with explicit policies.
- Improved snapshot backend options.
- Basic control-plane API service.
- Audit events for workspace and execution lifecycle.

### Out of Scope

- Full Kubernetes-style reconciliation system.
- Public plugin marketplace.
- Complex multi-cluster scheduling.
- Guaranteed hostile multi-tenant isolation without a documented hardening profile.

### V2 Exit Criteria

- The same Python SDK can run workspaces on Docker or Firecracker.
- Production users can choose a stronger isolation backend without changing agent code.
- Workspace data can persist outside the local machine.
- Security properties are documented per runtime.

## Phase 3: V3 — Distributed Workspace Platform

Primary goal: make Forge usable across teams, clusters, and heterogeneous infrastructure.

### Required Capabilities

- Kubernetes runtime driver.
- Remote worker model.
- Scheduler with placement based on resources, data locality, runtime capabilities, and policy.
- Multi-node workspace coordination.
- Incremental sync for large workspaces.
- Policy engine for network, secrets, resources, and allowed images.
- Stable internal extension interfaces.
- Observability: metrics, traces, structured logs, and event export.
- Role-based access control.
- Workspace templates.

### V3 Exit Criteria

- Forge can run workspaces across multiple worker nodes.
- Operators can enforce policy centrally.
- Teams can share workspace templates and snapshots.
- Runtime and storage backends can be extended without modifying core services.

## Phase 4: Ecosystem and Specification

Primary goal: turn proven interfaces into a stable ecosystem.

### Possible Capabilities

- Public plugin SDK.
- Versioned declarative workspace specification.
- Conformance tests for runtime drivers and workspace stores.
- Reference implementations for common backends.
- Extension registry.
- Compatibility matrix.
- Additional language-specific agent SDK helpers after the Python SDK is stable.

### Standardization Criteria

Forge should only publish a specification when:

- Multiple runtime drivers exist.
- Multiple workspace stores exist.
- The API has survived real usage.
- Backward compatibility rules are clear.
- Conformance tests exist.

## High-level Timeline Guidance

This roadmap intentionally avoids calendar promises. The priority order matters more than dates:

1. Useful local MVP.
2. Secure runtime path.
3. Distributed operation.
4. Stable ecosystem.
