# Low-level Design

## Objective

This document defines the V1 low-level design for making Forge compatible with LangChain Deep Agents sandbox backends.

## Package Layout

Proposed Python package structure:

```text
forge/
  __init__.py
  client.py
  models.py
  workspaces.py
  executions.py
  files.py
  artifacts.py
  langchain/
    __init__.py
    sandbox.py
    scope.py
```

## LangChain Adapter

Forge should expose a LangChain integration class:

```python
from forge.langchain import ForgeSandbox

backend = ForgeSandbox.from_thread(
    client=forge_client,
    thread_id="thread-123",
    image="python:3.13",
    idle_ttl_seconds=3600,
)
```

The adapter should implement the Deep Agents sandbox backend shape:

- filesystem methods for `ls`, `read`, `write`, `edit`, `glob`, `grep`, and optional `delete`,
- `execute(command: str)`,
- `upload_files(...)`,
- `download_files(...)`.

## Method Mapping

| Deep Agents operation | Forge operation |
| --- | --- |
| `execute(command)` | `workspace.exec(command, shell=True)` |
| `ls(path)` | `workspace.files.ls(path)` |
| `read(file_path, offset, limit)` | `workspace.files.read(file_path, offset=offset, limit=limit)` |
| `write(file_path, content)` | `workspace.files.write(file_path, content)` |
| `edit(file_path, old_string, new_string, replace_all)` | read + modify + write, or server-side edit operation |
| `glob(pattern, path)` | `workspace.files.glob(pattern, path=path)` |
| `grep(pattern, path, glob)` | `workspace.files.grep(pattern, path=path, glob=glob)` |
| `delete(file_path)` | `workspace.files.delete(file_path)` |
| `upload_files(files)` | batch write bytes into workspace |
| `download_files(paths)` | batch read bytes from workspace |

## Execution Result Mapping

Forge should preserve enough execution metadata to map cleanly into LangChain sandbox results:

```python
@dataclass(frozen=True)
class ForgeExecutionResult:
    output: str
    exit_code: int
    truncated: bool = False
    output_path: str | None = None
```

Rules:

1. Combine stdout and stderr into `output` for LangChain compatibility.
2. Preserve structured stdout/stderr separately in Forge's native execution API.
3. Return `exit_code` for success and failure.
4. Do not raise for normal command failures; return a structured result.
5. If output exceeds `max_output_bytes`, store full output in a workspace file and set `truncated=True` with `output_path`.

## File Transfer Result Mapping

Forge should support provider-style file transfer APIs for application code:

```python
@dataclass(frozen=True)
class ForgeFileUpload:
    path: str
    content: bytes


@dataclass(frozen=True)
class ForgeFileDownloadResult:
    path: str
    content: bytes | None = None
    error: str | None = None
```

## Scope Resolver

The adapter should use a scope resolver to map LangGraph runtime metadata to Forge workspace IDs.

```python
class ForgeScopeResolver:
    async def for_thread(self, thread_id: str) -> str:
        ...

    async def for_assistant(self, assistant_id: str) -> str:
        ...

    async def for_user_thread(self, user_id: str, thread_id: str) -> str:
        ...
```

The resolver should store mappings in Forge metadata so repeated agent calls reuse the same workspace.

## Workspace Creation Policy

When resolving a scope:

1. Look up an existing active workspace by scope metadata.
2. If found, reuse it.
3. If missing or expired, create a workspace with the requested image and runtime.
4. Attach TTL and quota metadata.
5. Return the workspace handle to the adapter.

## Permissions

The adapter should support path-level permissions before invoking Forge file operations:

```python
@dataclass(frozen=True)
class ForgePathPermission:
    path: str
    read: bool = True
    write: bool = False
    execute: bool = False
```

V1 should implement simple allow/deny checks in the adapter. Later versions can move policy enforcement into the Forge API service.

## Timeout and Output Defaults

Recommended V1 defaults:

- command timeout: 120 seconds,
- max output bytes: 100,000,
- idle TTL: 3600 seconds for thread-scoped workspaces,
- network: configurable, disabled by default for untrusted workloads where possible.

## Error Handling

LangChain-compatible adapter methods should return structured result objects with error fields where the backend protocol expects them. Normal command failures should not raise exceptions. Infrastructure failures may raise Forge-specific exceptions, but the adapter should convert expected filesystem and command errors into protocol results.

## V1 Acceptance Tests

V1 compatibility is complete when automated tests prove that:

1. `ForgeSandbox` can be passed as `backend=` to `create_deep_agent(...)`.
2. The agent can call filesystem tools backed by Forge.
3. The agent can call `execute` and receive output plus exit code.
4. `upload_files` can seed a workspace before a run.
5. `download_files` can retrieve generated artifacts after a run.
6. Thread-scoped workspace reuse works across multiple invocations.
7. TTL cleanup deletes or archives idle workspaces.
8. Secrets are not injected by default.
