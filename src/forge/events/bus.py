"""In-process pub/sub for :class:`forge.models.ForgeEvent`.

Not durable, not cross-process — the HTTP layer bridges to SSE for external
consumers. Keep the interface small so a durable/queued backend can slot in
later without touching services.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from forge.models import ForgeEvent


@dataclass(slots=True)
class _Subscription:
    """One live subscription — a bounded queue and an optional filter."""

    queue: asyncio.Queue[ForgeEvent]
    predicate: Callable[[ForgeEvent], bool] | None = None
    lost: int = 0


@dataclass(slots=True)
class EventBus:
    """Minimal in-process pub/sub.

    Publishers call :meth:`publish`; subscribers get an async iterator via
    :meth:`subscribe`. Slow subscribers drop events after their queue fills,
    but the ``lost`` counter is exposed so the HTTP layer can hint to clients.
    """

    _subs: list[_Subscription] = field(default_factory=list)
    _queue_size: int = 256

    async def publish(self, event: ForgeEvent) -> None:
        """Fan out one event. Never raises; slow subs drop instead of blocking."""
        for sub in list(self._subs):
            if sub.predicate is not None and not sub.predicate(event):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.lost += 1

    async def subscribe(
        self,
        *,
        kinds: set[str] | None = None,
        workspace_id: str | None = None,
        execution_id: str | None = None,
    ) -> AsyncIterator[ForgeEvent]:
        """Yield events matching the filter until the caller stops iterating.

        The subscription is torn down when the generator is closed — either
        by the caller ``break``-ing out of the loop or by the enclosing
        ``async with`` finishing.
        """

        def _match(ev: ForgeEvent) -> bool:
            if kinds is not None and ev.kind not in kinds:
                return False
            if workspace_id is not None and ev.workspace_id != workspace_id:
                return False
            return not (execution_id is not None and ev.execution_id != execution_id)

        sub = _Subscription(queue=asyncio.Queue(maxsize=self._queue_size), predicate=_match)
        self._subs.append(sub)
        try:
            while True:
                ev = await sub.queue.get()
                yield ev
        finally:
            self._subs.remove(sub)


__all__ = ["EventBus"]
