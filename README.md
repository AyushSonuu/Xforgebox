# Forge

Forge is an open-source workspace runtime for AI agents: persistent filesystems, isolated command execution, snapshots, artifacts, and pluggable runtime backends.

## Vision

AI agents need more than one-shot command execution. They need real workspaces where they can create files, install dependencies, run tests, preserve state, export artifacts, and resume later.

Forge aims to provide that runtime layer while allowing different execution backends over time:

- Docker for local development and quick adoption.
- Firecracker for stronger production isolation.
- Kubernetes for distributed infrastructure.
- Future runtimes such as WASM, remote workers, or specialized sandboxes.

## Documentation

- [Product Definition](docs/product.md)
- [Roadmap](docs/roadmap.md)
- [MVP Design](docs/mvp-design.md)
- [V2 Design Direction](docs/v2-design.md)
- [V3 Design Direction](docs/v3-design.md)
- [Architecture Review](docs/review.md)

## Current Status

Forge is currently in the product and architecture definition phase. The recommended first implementation is a narrow MVP with local workspace storage, Docker-based execution, command log streaming, snapshots, artifacts, a CLI, and a Python SDK.

## Guiding Principle

Start small, prove the workspace abstraction, then add stronger isolation and distributed operation.
