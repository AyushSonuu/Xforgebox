"""Docker runtime driver (aiodocker-backed).

Bind-mounts ``{data_root}/workspaces`` → ``/workspaces`` at container start
time; every ``exec`` runs a tiny ``/usr/local/bin/forge-run`` helper that
``cd``s into the workspace so user commands always see ``cwd=/workspace``.
This is the MVP's answer to the multi-workspace-per-container tradeoff — see
amendment A1 in ``docs/mvp-implementation-notes.md``.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import tarfile
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import aiodocker
import aiodocker.exceptions

from forge.drivers.base import (
    EnvironmentHandle,
    Mount,
)
from forge.errors import (
    ContainerStartError,
    ExecTimeoutError,
    RuntimeDriverError,
)
from forge.models import ExecutionResult, LogEvent, ResourceLimits, RuntimeCapabilities

log = logging.getLogger(__name__)


# The forge-run entrypoint helper installed into every pooled container. Keeps
# cwd handling out of `docker exec` invocations and lets the driver stay a
# generic runtime backend. `FORGE_WORKSPACE_DIR` is injected per exec.
_FORGE_RUN_SCRIPT = b"""#!/bin/sh
set -e
if [ -z "${FORGE_WORKSPACE_DIR:-}" ]; then
    echo "forge-run: FORGE_WORKSPACE_DIR unset" >&2
    exit 64
fi
# Refresh the /workspace symlink each time - the workspace bound to this
# container changes between execs.
ln -sfn "$FORGE_WORKSPACE_DIR" /workspace 2>/dev/null || true
cd /workspace
exec "$@"
"""

_FORGE_RUN_PATH = "usr/local/bin/forge-run"
_LABEL_MANAGED_BY = "forge.managed-by"
_LABEL_MANAGED_VALUE = "forge"


class DockerDriver:
    """``RuntimeDriver`` implementation on top of the local Docker daemon.

    Not safe for hostile multi-tenant workloads: all pooled containers share
    the ``/workspaces`` tree via a single bind mount. See the MVP security
    section in ``docs/mvp-design.md``.
    """

    name = "docker"

    def __init__(
        self,
        *,
        docker: aiodocker.Docker | None = None,
        container_label_prefix: str = "forge-",
    ) -> None:
        """Args:
            docker: optional pre-built ``aiodocker.Docker`` client (tests inject).
            container_label_prefix: name prefix applied to created containers
                for easy identification in ``docker ps``.
        """
        # Ownership: if the caller supplied a client we do not close it on
        # cleanup — they manage its lifetime.
        self._docker_owned = docker is None
        self._docker = docker or aiodocker.Docker()
        self._label_prefix = container_label_prefix

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the aiodocker client if we created it."""
        if self._docker_owned:
            await self._docker.close()

    async def __aenter__(self) -> DockerDriver:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            isolation="container",
            snapshots=False,
            pause_resume=False,
            network_control=True,
            resource_limits=True,
            hot_attach_volume=False,
            gpu=False,
            supports_streaming_logs=True,
        )

    # ------------------------------------------------------------------
    # Environment lifecycle
    # ------------------------------------------------------------------

    async def create_environment(
        self,
        *,
        image: str,
        mounts: list[Mount],
        resources: ResourceLimits | None = None,
    ) -> EnvironmentHandle:
        """Boot a pooled container.

        - Pulls the image if it's not present locally.
        - Starts the container with ``tail -f /dev/null`` as PID 1 so ``exec``
          keeps working.
        - Uploads the ``forge-run`` helper via a tiny tar archive.
        """
        await self._ensure_image(image)
        container_name = f"{self._label_prefix}{uuid.uuid4().hex[:12]}"

        host_config = _build_host_config(mounts, resources)
        cfg: dict[str, Any] = {
            "Image": image,
            "Cmd": ["tail", "-f", "/dev/null"],
            "Tty": False,
            "AttachStdin": False,
            "AttachStdout": False,
            "AttachStderr": False,
            "Labels": {_LABEL_MANAGED_BY: _LABEL_MANAGED_VALUE},
            "HostConfig": host_config,
            # Set a benign default WorkingDir — actual cwd is enforced by
            # forge-run at exec time so multiple workspaces can share one
            # container safely.
            "WorkingDir": "/",
        }
        try:
            container = await self._docker.containers.create_or_replace(
                name=container_name,
                config=cfg,
            )
            await container.start()
        except aiodocker.exceptions.DockerError as e:
            raise ContainerStartError(
                f"docker refused to start container for image {image!r}: {e}"
            ) from e

        try:
            await self._install_forge_run(container)
        except Exception:
            # Roll back the container so we never hand out a broken env.
            with contextlib.suppress(aiodocker.exceptions.DockerError):
                await container.kill()
            with contextlib.suppress(aiodocker.exceptions.DockerError):
                await container.delete(force=True)
            raise

        log.info(
            "docker: started pooled container %s (image=%s)",
            container_name,
            image,
        )
        return EnvironmentHandle(
            id=container.id,
            image=image,
            driver=self.name,
            runtime_labels={"name": container_name},
        )

    async def destroy_environment(self, environment_id: str) -> None:
        """Stop + remove a container. Idempotent."""
        try:
            container = self._docker.containers.container(environment_id)
            with contextlib.suppress(aiodocker.exceptions.DockerError):
                await container.stop(t=1)
            await container.delete(force=True)
        except aiodocker.exceptions.DockerError as e:
            # 404 is fine — someone already cleaned up.
            if getattr(e, "status", None) == 404:
                return
            raise RuntimeDriverError(
                f"failed to destroy container {environment_id!r}: {e}"
            ) from e

    async def health_check(self, environment_id: str) -> bool:
        """Run ``true`` inside the container; return True on exit 0."""
        try:
            container = self._docker.containers.container(environment_id)
            info = await container.show()
            if not info.get("State", {}).get("Running", False):
                return False
            exec_ = await container.exec(
                cmd=["true"],
                stdout=True,
                stderr=True,
            )
            async with exec_.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
            inspect = await exec_.inspect()
            return bool(inspect.get("ExitCode") == 0)
        except aiodocker.exceptions.DockerError:
            return False
        except Exception:
            log.exception("docker: health check failed")
            return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def exec_in_environment(
        self,
        environment_id: str,
        command: list[str],
        *,
        workspace_id: str,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None,
        max_output_bytes: int,
    ) -> ExecutionResult:
        """Buffered execution — collect output up to ``max_output_bytes``."""
        env_dict = _build_exec_env(workspace_id, env)
        wrapped_cmd = ["forge-run", *command]
        container = self._docker.containers.container(environment_id)

        try:
            exec_ = await container.exec(
                cmd=wrapped_cmd,
                stdout=True,
                stderr=True,
                environment=[f"{k}={v}" for k, v in env_dict.items()],
            )
        except aiodocker.exceptions.DockerError as e:
            raise RuntimeDriverError(f"docker exec create failed: {e}") from e

        started_at = time.monotonic()
        exec_id = f"ex_{uuid.uuid4().hex[:16]}"
        buf = bytearray()
        truncated = False
        timed_out = False

        async def _drain() -> None:
            nonlocal truncated
            async with exec_.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    chunk = msg.data
                    if not truncated:
                        remaining = max_output_bytes - len(buf)
                        if remaining <= 0:
                            truncated = True
                            continue
                        buf.extend(chunk[:remaining])
                        if len(buf) >= max_output_bytes:
                            truncated = True
                    # else: keep draining to let the process finish quickly

        try:
            if timeout_seconds is not None and timeout_seconds > 0:
                await asyncio.wait_for(_drain(), timeout=timeout_seconds)
            else:
                await _drain()
        except TimeoutError:
            timed_out = True
        except aiodocker.exceptions.DockerError as e:
            raise RuntimeDriverError(f"docker exec stream failed: {e}") from e

        duration_ms = int((time.monotonic() - started_at) * 1000)
        exit_code = -1
        if not timed_out:
            try:
                inspect = await exec_.inspect()
                raw = inspect.get("ExitCode")
                # Docker returns null while the exec is still starting; clamp
                # to a sentinel so callers see something deterministic.
                exit_code = int(raw) if raw is not None else -1
            except aiodocker.exceptions.DockerError:
                exit_code = -1

        return ExecutionResult(
            execution_id=exec_id,
            output=buf.decode("utf-8", errors="replace"),
            exit_code=124 if timed_out else exit_code,
            truncated=truncated,
            output_path=None,
            duration_ms=duration_ms,
        )

    async def stream_exec_in_environment(
        self,
        environment_id: str,
        command: list[str],
        *,
        workspace_id: str,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[LogEvent]:
        """Yield one :class:`LogEvent` per chunk of stdout/stderr."""
        env_dict = _build_exec_env(workspace_id, env)
        wrapped_cmd = ["forge-run", *command]
        container = self._docker.containers.container(environment_id)
        try:
            exec_ = await container.exec(
                cmd=wrapped_cmd,
                stdout=True,
                stderr=True,
                environment=[f"{k}={v}" for k, v in env_dict.items()],
            )
        except aiodocker.exceptions.DockerError as e:
            raise RuntimeDriverError(f"docker exec create failed: {e}") from e

        exec_id = f"ex_{uuid.uuid4().hex[:16]}"
        deadline = (
            None if timeout_seconds is None or timeout_seconds <= 0
            else time.monotonic() + timeout_seconds
        )

        try:
            async with exec_.start(detach=False) as stream:
                while True:
                    remaining: float | None = None
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ExecTimeoutError(
                                f"exec exceeded timeout of {timeout_seconds}s"
                            )
                    read = stream.read_out()
                    msg = (
                        await asyncio.wait_for(read, timeout=remaining)
                        if remaining is not None
                        else await read
                    )
                    if msg is None:
                        return
                    stream_name = "stderr" if msg.stream == 2 else "stdout"
                    yield LogEvent(
                        execution_id=exec_id,
                        stream=stream_name,
                        ts=datetime.now(UTC),
                        data=msg.data.decode("utf-8", errors="replace"),
                    )
        except TimeoutError as e:
            raise ExecTimeoutError(
                f"exec exceeded timeout of {timeout_seconds}s"
            ) from e
        except aiodocker.exceptions.DockerError as e:
            raise RuntimeDriverError(f"docker exec stream failed: {e}") from e

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_image(self, image: str) -> None:
        """Pull ``image`` if it's not present locally.

        Uses a low-level pull to avoid the "reference does not exist" edge
        cases in older aiodocker versions.
        """
        try:
            await self._docker.images.inspect(image)
            return
        except aiodocker.exceptions.DockerError as e:
            if getattr(e, "status", None) != 404:
                raise
        log.info("docker: pulling image %s", image)
        try:
            await self._docker.images.pull(image)
        except aiodocker.exceptions.DockerError as e:
            raise RuntimeDriverError(f"failed to pull image {image!r}: {e}") from e

    async def _install_forge_run(self, container: Any) -> None:
        """Upload the ``forge-run`` entrypoint into a running container."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=_FORGE_RUN_PATH)
            info.size = len(_FORGE_RUN_SCRIPT)
            info.mode = 0o755
            info.uid = 0
            info.gid = 0
            tf.addfile(info, io.BytesIO(_FORGE_RUN_SCRIPT))
        buf.seek(0)
        await container.put_archive(path="/", data=buf.getvalue())


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _build_host_config(
    mounts: list[Mount],
    resources: ResourceLimits | None,
) -> dict[str, Any]:
    """Translate portable ``Mount``/``ResourceLimits`` into docker's HostConfig dict."""
    hc: dict[str, Any] = {}
    if mounts:
        binds = [
            f"{m.source}:{m.target}:{'ro' if m.read_only else 'rw'}"
            for m in mounts
        ]
        hc["Binds"] = binds
    if resources is not None:
        if resources.cpu is not None:
            hc["NanoCpus"] = int(resources.cpu * 1_000_000_000)
        if resources.memory is not None:
            hc["Memory"] = _parse_memory(resources.memory)
    return hc


def _parse_memory(s: str) -> int:
    """Parse ``512Mi``/``2Gi``-style values into bytes."""
    s = s.strip()
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    suffix = s[-2:]
    if suffix.endswith("i") and suffix[0] in units:
        return int(float(s[:-2]) * units[suffix[0]])
    if s and s[-1] in units:
        return int(float(s[:-1]) * units[s[-1]])
    return int(s)


def _build_exec_env(workspace_id: str, env: dict[str, str] | None) -> dict[str, str]:
    """Compose the env dict handed to ``forge-run``."""
    out = {
        "FORGE_WORKSPACE_DIR": f"/workspaces/{workspace_id}",
        "FORGE_WORKSPACE_ID": workspace_id,
        "PWD": "/workspace",
    }
    if env:
        out.update(env)
    return out


__all__ = ["DockerDriver"]
