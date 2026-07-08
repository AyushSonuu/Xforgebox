"""Concurrent-agents demo — proves the container pool shares resources.

Spawns 20 simulated agents. Each does 5 short-command execs. The pool is
configured with ``max_size=4`` — so 20 agents share 4 warm containers.

Run::

    python examples/concurrent_agents_demo.py

Requires the docker daemon and the ``python:3.14-slim`` image (or override
``FORGE_TEST_IMAGE``).
"""
from __future__ import annotations

import asyncio
import os
import statistics
import tempfile
import time
from pathlib import Path

from forge.client import Forge
from forge.config import make_config
from forge.models import PoolConfig

IMAGE = os.environ.get("FORGE_TEST_IMAGE", "python:3.14-slim")
NUM_AGENTS = 20
EXECS_PER_AGENT = 5
MAX_POOL = 4


async def agent_run(forge: Forge, name: str) -> list[float]:
    """One 'agent' that does EXECS_PER_AGENT tool-call bursts."""
    ws = await forge.workspaces.create(image=IMAGE, name=name)
    latencies: list[float] = []
    for i in range(EXECS_PER_AGENT):
        started = time.perf_counter()
        result = await ws.exec(
            ["sh", "-c", f"echo agent={name} step={i}"],
            timeout_seconds=15,
        )
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        assert result.exit_code == 0, f"failed: {result}"
    return latencies


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_config(
            Path(tmp),
            default_pool=PoolConfig(
                image=IMAGE,
                min_idle=1,
                max_size=MAX_POOL,
                idle_ttl_seconds=60,
                exec_timeout_seconds=30,
                lease_wait_timeout_seconds=30.0,
            ),
        )
        async with Forge.local(config=cfg) as forge:
            print(
                f"[demo] launching {NUM_AGENTS} agents against "
                f"pool max_size={MAX_POOL}, {EXECS_PER_AGENT} execs each..."
            )
            t0 = time.perf_counter()
            all_lat = await asyncio.gather(
                *(agent_run(forge, f"agent-{i}") for i in range(NUM_AGENTS))
            )
            wall = time.perf_counter() - t0

            latencies = [lat for row in all_lat for lat in row]
            stats = await forge.pool_status()
            top = stats[0]
            print(
                f"[demo] {NUM_AGENTS} agents / {len(latencies)} execs / "
                f"wall={wall:.2f}s / max concurrent containers={top.total} "
                f"(bound={MAX_POOL}) / p50={statistics.median(latencies):.1f}ms / "
                f"p95={_pct(latencies, 95):.1f}ms / total_leases={top.total_leases}"
            )


def _pct(xs: list[float], p: int) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


if __name__ == "__main__":
    asyncio.run(main())
