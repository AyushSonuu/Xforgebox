# Forge Product Definition

## One-line Description

Forge is an open-source workspace runtime for AI agents: persistent filesystems, isolated command execution, snapshots, artifacts, and pluggable runtime backends.

## Problem

AI agents increasingly need more than stateless command execution. They create projects, edit files, install dependencies, run tests, start servers, inspect logs, and resume work later. Existing primitives solve only parts of this workflow:

- Containers provide convenient packaging, but are not a complete agent workspace model.
- MicroVMs provide stronger isolation, but do not define persistence, snapshots, artifacts, or agent-friendly APIs.
- Kubernetes orchestrates infrastructure, but is too low-level for most agent developers.
- Hosted sandbox products are useful, but are often closed, managed, or tied to one backend.

Forge exists to provide the missing runtime layer between AI agents and execution infrastructure.

## Target Users

### Primary Users

- Developers building AI coding agents.
- Teams building internal code-execution agents.
- AI evaluation platforms that need reproducible workspaces.
- Companies running untrusted or semi-trusted generated code.
- Infrastructure teams standardizing agent execution across local, cloud, and secure runtimes.

### Secondary Users

- Developer tool companies needing programmable workspaces.
- CI-style systems for short-lived dynamic environments.
- Research teams running repeatable code-agent experiments.

## Non-goals

Forge is not intended to be:

- A Kubernetes replacement.
- A Docker replacement.
- A hosted IDE.
- A general cloud application platform.
- A new image format.
- A language-specific package manager.

Forge should sit above infrastructure backends and make them usable as agent workspaces. The reference implementation should be Python-first so the project aligns naturally with the AI tooling ecosystem.

## Product Principles

1. **Workspace-first**: The primary abstraction is a persistent workspace, not a container or VM.
2. **Runtime-agnostic**: Docker, Firecracker, Kubernetes, WASM, and remote runtimes should be interchangeable behind a stable API.
3. **Local-first adoption**: The first experience should work on a developer laptop using Docker and local storage.
4. **Production isolation path**: The architecture must support stronger isolation through Firecracker or comparable runtimes.
5. **Small stable core**: The core should stay narrow: workspaces, environments, executions, snapshots, artifacts, and events.
6. **Extensible after validation**: Internal interfaces should be clean first; external plugin APIs should come after multiple implementations prove the shape.
7. **Python-first implementation**: The reference implementation, CLI, and first SDK should be built in Python.
8. **Do not overclaim security**: Forge can provide isolation controls, but each runtime must document its actual security boundary.

## Core Concepts

### Workspace

A persistent filesystem and metadata record used by an agent or user. A workspace survives across executions and can be snapshotted, restored, archived, or destroyed.

### Environment

A live execution environment attached to a workspace. It may be backed by Docker, Firecracker, Kubernetes, local processes, or another runtime.

### Execution

A command or process running inside an environment. Executions expose stdout, stderr, exit code, status, timeout, and cancellation.

### Snapshot

A point-in-time record of workspace state. Early snapshots may be tar/zstd archives; later versions may support copy-on-write filesystems, Firecracker snapshots, remote snapshots, and incremental sync.

### Artifact

A file or output intentionally exported from a workspace, such as a report, build result, image, log, dataset sample, or test output.

### Runtime Driver

A backend implementation that can create environments and run commands. Examples include Docker, Firecracker, Kubernetes, and WASM.

### Workspace Store

A storage implementation for workspace data. Examples include local filesystem, S3-compatible object storage, Git-backed storage, and distributed filesystems.

## Product Positioning

Forge should be positioned as:

> The open-source workspace runtime for AI agents.

It should not initially be marketed as:

> Kubernetes for AI agents.

That analogy is useful for long-term strategy, but it may imply unnecessary complexity. The user-facing promise should be simpler:

> Create a workspace, run code inside it, persist files, snapshot state, and choose the runtime backend that fits your trust and scale requirements.

## Differentiation

### Compared with Docker

Docker is a runtime backend. Forge adds an agent-facing workspace model, snapshots, artifacts, execution APIs, and a path to other runtimes.

### Compared with Firecracker

Firecracker is a secure microVM VMM. Forge can use Firecracker, but also defines workspace persistence, image resolution, command execution, artifacts, events, and developer APIs.

### Compared with Kubernetes

Kubernetes schedules infrastructure. Forge defines higher-level AI workspace lifecycle APIs and can use Kubernetes as a backend.

### Compared with hosted sandbox APIs

Hosted sandboxes optimize convenience. Forge should optimize openness, self-hosting, embeddability, runtime choice, and workspace portability.

## Success Criteria

Forge is successful if developers can:

1. Create a workspace from an image.
2. Write files into it.
3. Execute commands with streamed logs.
4. Persist state between commands.
5. Snapshot and restore the workspace.
6. Run locally with Docker.
7. Upgrade to stronger isolation with Firecracker without rewriting agent code.
8. Use the same API across local, secure, and distributed deployments.
