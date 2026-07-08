"""``ContainerPool`` — the shared execution resource across agent instances.

Per-image sub-pools of warm runtime environments; each ``session()`` hands out
a workspace-bound :class:`~forge.pool.lease._PooledSession`. Callers never see
container IDs or bind-mount paths. See amendment A1 in
``docs/mvp-implementation-notes.md``.

Concurrency model:

- One ``_SubPool`` per image; a top-level ``asyncio.Lock`` protects the
  ``image -> _SubPool`` dict.
- Each ``_SubPool`` protects its own slot accounting with a lock and uses an
  ``asyncio.Queue`` for handing off idle environments.
- Acquire behaviour:
  * If an idle env is available, use it (post health check).
  * If under ``max_size``, create a new env.
  * Otherwise wait up to ``lease_wait_timeout_seconds`` for a release.
- Release always returns the env to the idle queue unless the caller flagged
  it dead (health-kill path).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from forge.config import ForgeConfig
from forge.drivers.base import EnvironmentHandle, Mount, RuntimeDriver
from forge.errors import PoolClosedError, PoolExhaustedError
from forge.models import PoolConfig, PoolStats
from forge.pool.lease import _PooledSession

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _IdleEntry:
    """One entry in the idle queue: env + last-used timestamp."""

    env: EnvironmentHandle
    idle_since: float


@dataclass(slots=True)
class _SubPool:
    """State + accounting for one image."""

    config: PoolConfig
    idle: asyncio.Queue[_IdleEntry] = field(default_factory=asyncio.Queue)
    in_use: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Counters, exposed via PoolStats.
    total_leases: int = 0
    total_wait_ms: int = 0
    total_kills: int = 0

    @property
    def total(self) -> int:
        return self.idle.qsize() + len(self.in_use)


class ContainerPool:
    """Per-image warm pools of runtime environments.

    Not thread-safe: intended to be used from a single asyncio event loop.
    Instantiate one per daemon process.
    """

    def __init__(
        self,
        *,
        driver: RuntimeDriver,
        config: ForgeConfig,
    ) -> None:
        """Args:
            driver: the underlying :class:`~forge.drivers.base.RuntimeDriver`.
            config: :class:`~forge.config.ForgeConfig` — read to pick the mount
                point (``config.workspaces_root``) and per-image ``PoolConfig``s.
        """
        self._driver = driver
        self._config = config
        self._pools: dict[str, _SubPool] = {}
        self._pools_lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False
        self._reaper_interval_s: float = 5.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, warm_images: list[str] | None = None) -> None:
        """Boot the reaper and warm the default image.

        Callers can pass ``warm_images`` to pre-warm additional images (each
        gets its own sub-pool with the config from ``config.pool_config_for``).
        """
        if self._closed:
            raise PoolClosedError("pool has been shut down")
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reaper_loop(), name="forge-pool-reaper")

        images = [self._config.default_pool.image, *(warm_images or [])]
        # De-dupe while preserving order.
        seen: set[str] = set()
        for image in images:
            if image in seen:
                continue
            seen.add(image)
            await self._ensure_subpool(image)
        # Best-effort warm to min_idle.
        for image in seen:
            sp = self._pools[image]
            await self._warm(sp)

    async def shutdown(self) -> None:
        """Cancel the reaper and destroy every managed environment."""
        if self._closed:
            return
        self._closed = True
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        # Tear everything down. Draining idle first minimises time spent
        # blocking new leases (which we now reject anyway).
        for sp in self._pools.values():
            while not sp.idle.empty():
                entry = sp.idle.get_nowait()
                with contextlib.suppress(Exception):
                    await self._driver.destroy_environment(entry.env.id)
            for env_id in list(sp.in_use):
                with contextlib.suppress(Exception):
                    await self._driver.destroy_environment(env_id)
            sp.in_use.clear()

    # ------------------------------------------------------------------
    # Session API — the sole public entry point
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session(
        self,
        *,
        workspace_id: str,
        image: str,
    ) -> AsyncIterator[_PooledSession]:
        """Lease a runtime environment for one workspace × image burst.

        The returned session's ``exec`` and ``stream_exec`` may be called any
        number of times. On context exit the environment goes back to the pool.
        """
        if self._closed:
            raise PoolClosedError("pool has been shut down")
        sp = await self._ensure_subpool(image)
        env = await self._acquire(sp)
        session = _PooledSession(
            workspace_id=workspace_id,
            image=image,
            _env=env,
            _driver=self._driver,
            _config=sp.config,
        )
        try:
            yield session
        finally:
            session._close()  # noqa: SLF001 — the pool owns this state
            await self._release(sp, env)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self, image: str | None = None) -> list[PoolStats]:
        """Return per-image statistics — used by the /pool/status endpoint."""
        pools = (
            {image: self._pools[image]} if image and image in self._pools else self._pools
        )
        out: list[PoolStats] = []
        for name, sp in pools.items():
            out.append(
                PoolStats(
                    image=name,
                    idle=sp.idle.qsize(),
                    in_use=len(sp.in_use),
                    total=sp.total,
                    max_size=sp.config.max_size,
                    min_idle=sp.config.min_idle,
                    total_leases=sp.total_leases,
                    total_lease_wait_ms=sp.total_wait_ms,
                    total_health_kills=sp.total_kills,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_subpool(self, image: str) -> _SubPool:
        """Return (or lazily create) the sub-pool for ``image``."""
        async with self._pools_lock:
            sp = self._pools.get(image)
            if sp is not None:
                return sp
            cfg = self._config.pool_config_for(image)
            sp = _SubPool(config=cfg)
            self._pools[image] = sp
            return sp

    async def _warm(self, sp: _SubPool) -> None:
        """Pre-create environments up to ``min_idle`` for a sub-pool."""
        needed = sp.config.min_idle - sp.total
        for _ in range(max(0, needed)):
            try:
                env = await self._create_env(sp)
            except Exception:
                log.exception("pool: warm-up failed for image %s", sp.config.image)
                return
            sp.idle.put_nowait(_IdleEntry(env=env, idle_since=time.monotonic()))

    async def _acquire(self, sp: _SubPool) -> EnvironmentHandle:
        """Pull an idle env, or create a new one, or wait for a release.

        All slot-accounting mutations happen under ``sp.lock``. The lock is
        held across the driver's ``create_environment`` call — that call can
        take hundreds of ms, but serialising it is the price of not
        over-provisioning.
        """
        started = time.monotonic()
        deadline = started + sp.config.lease_wait_timeout_seconds
        while True:
            # All lookups + slot changes happen under the sub-pool lock so a
            # winner of the idle queue can't be double-counted by a concurrent
            # "create new" path.
            async with sp.lock:
                # 1. Try the idle queue first.
                entry: _IdleEntry | None
                try:
                    entry = sp.idle.get_nowait()
                except asyncio.QueueEmpty:
                    entry = None
                if entry is not None:
                    # Reserve the slot before we release the lock for the
                    # health check.
                    sp.in_use.add(entry.env.id)
                    lease_env_id = entry.env.id
                else:
                    lease_env_id = None

                # 2. Nothing idle: create a fresh env if we have room.
                if entry is None and sp.total < sp.config.max_size:
                    placeholder = f"__pending__{time.monotonic_ns()}"
                    sp.in_use.add(placeholder)
                    try:
                        env = await self._create_env(sp)
                    except Exception:
                        sp.in_use.discard(placeholder)
                        raise
                    sp.in_use.discard(placeholder)
                    sp.in_use.add(env.id)
                    sp.total_leases += 1
                    sp.total_wait_ms += int((time.monotonic() - started) * 1000)
                    return env

            # 3. Health-check anything we pulled from the idle queue (do this
            #    outside the lock — health_check may take ~50ms).
            if entry is not None:
                if await self._driver.health_check(entry.env.id):
                    async with sp.lock:
                        sp.total_leases += 1
                        sp.total_wait_ms += int((time.monotonic() - started) * 1000)
                    return entry.env
                # Unhealthy: dispose and retry.
                async with sp.lock:
                    if lease_env_id is not None:
                        sp.in_use.discard(lease_env_id)
                    sp.total_kills += 1
                with contextlib.suppress(Exception):
                    await self._driver.destroy_environment(entry.env.id)
                continue

            # 4. At max_size with nothing idle: wait for a release.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PoolExhaustedError(
                    f"pool for image {sp.config.image!r} exhausted "
                    f"(max_size={sp.config.max_size}); wait timed out after "
                    f"{sp.config.lease_wait_timeout_seconds}s"
                )
            try:
                entry = await asyncio.wait_for(sp.idle.get(), timeout=remaining)
            except TimeoutError:
                raise PoolExhaustedError(
                    f"pool for image {sp.config.image!r} exhausted"
                ) from None
            # Push it back and let the top-of-loop re-run health check.
            sp.idle.put_nowait(entry)

    async def _release(self, sp: _SubPool, env: EnvironmentHandle) -> None:
        """Return an env to the idle queue (best effort).

        Holds the container's slot in ``in_use`` until we've decided its
        fate — otherwise a concurrent acquire in step 2 sees room and
        spawns a new env, blowing past ``max_size``.
        """
        # Shutdown path: destroy without ever putting back.
        if self._closed:
            async with sp.lock:
                sp.in_use.discard(env.id)
            with contextlib.suppress(Exception):
                await self._driver.destroy_environment(env.id)
            return

        # Health check while the env is still counted in in_use. This keeps
        # sp.total accurate for anyone else trying to acquire.
        alive = False
        try:
            alive = await self._driver.health_check(env.id)
        except Exception:
            alive = False

        async with sp.lock:
            sp.in_use.discard(env.id)
            if alive:
                sp.idle.put_nowait(_IdleEntry(env=env, idle_since=time.monotonic()))
            else:
                sp.total_kills += 1
        if not alive:
            with contextlib.suppress(Exception):
                await self._driver.destroy_environment(env.id)

    async def _create_env(self, sp: _SubPool) -> EnvironmentHandle:
        """Create one new environment with the standard workspaces bind mount."""
        return await self._driver.create_environment(
            image=sp.config.image,
            mounts=[
                Mount(
                    source=str(self._config.workspaces_root),
                    target=sp.config.workspaces_mount,
                    read_only=False,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # Reaper
    # ------------------------------------------------------------------

    async def _reaper_loop(self) -> None:
        """Periodic background task that tears down idle envs past TTL."""
        while not self._closed:
            try:
                await asyncio.sleep(self._reaper_interval_s)
            except asyncio.CancelledError:
                return
            try:
                await self._reap_once()
            except Exception:
                log.exception("pool: reaper iteration failed")

    async def _reap_once(self) -> None:
        """One reaper pass: kill idle envs past TTL, then warm to min_idle."""
        now = time.monotonic()
        for sp in list(self._pools.values()):
            # Pull everything idle so we can filter without racing get_nowait.
            drained: list[_IdleEntry] = []
            while True:
                try:
                    drained.append(sp.idle.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Keep at least min_idle (the youngest first).
            drained.sort(key=lambda e: e.idle_since, reverse=True)
            keep_target = max(sp.config.min_idle - len(sp.in_use), 0)
            kept, dead = drained[:keep_target], drained[keep_target:]

            for entry in kept:
                sp.idle.put_nowait(entry)

            # Kill anything past TTL from the "dead" pile, and also kill
            # anything from the "kept" pile that has aged out (that means we
            # over-kept but idle exceeded TTL).
            surviving_kept: list[_IdleEntry] = []
            for entry in kept:
                if now - entry.idle_since > sp.config.idle_ttl_seconds:
                    # Age out AND we already put it back — remove it.
                    with contextlib.suppress(asyncio.QueueEmpty):
                        sp.idle.get_nowait()
                    with contextlib.suppress(Exception):
                        await self._driver.destroy_environment(entry.env.id)
                    continue
                surviving_kept.append(entry)
            # Adjust the queue to only contain "surviving_kept" — cheap redo:
            while True:
                try:
                    sp.idle.get_nowait()
                except asyncio.QueueEmpty:
                    break
            for entry in surviving_kept:
                sp.idle.put_nowait(entry)

            for entry in dead:
                with contextlib.suppress(Exception):
                    await self._driver.destroy_environment(entry.env.id)

            # Top up if we now have room.
            await self._warm(sp)


__all__ = ["ContainerPool"]
