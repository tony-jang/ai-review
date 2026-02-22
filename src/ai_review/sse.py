"""SSE event broker for real-time updates."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class SSEEvent:
    event: str
    data: dict

    def format(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data)}\n\n"


class SSEBroker:
    """Simple pub/sub broker for SSE events."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[SSEEvent | None]] = []

    def publish(self, event: str, data: dict) -> None:
        sse_event = SSEEvent(event=event, data=data)
        for q in self._queues:
            q.put_nowait(sse_event)

    async def subscribe(self) -> AsyncIterator[SSEEvent]:
        q: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        self._queues.append(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    break
                yield event
        finally:
            if q in self._queues:
                self._queues.remove(q)

    def disconnect_all(self) -> None:
        for q in self._queues:
            q.put_nowait(None)
