"""Unit tests for ai_review.sse module."""

from __future__ import annotations

import asyncio
import json

import pytest

from ai_review.sse import SSEBroker, SSEEvent


class TestSSEEvent:
    def test_event_format_basic(self):
        event = SSEEvent(event="foo", data={"key": "value"})
        formatted = event.format()
        assert formatted == f'event: foo\ndata: {json.dumps({"key": "value"})}\n\n'

    def test_event_format_nested_data(self):
        data = {"outer": {"inner": [1, 2, 3]}, "flag": True}
        event = SSEEvent(event="update", data=data)
        formatted = event.format()
        assert formatted.startswith("event: update\ndata: ")
        assert formatted.endswith("\n\n")
        parsed = json.loads(formatted.split("data: ", 1)[1].strip())
        assert parsed == data


class TestSSEBroker:
    @pytest.mark.asyncio
    async def test_single_subscriber_receives(self):
        broker = SSEBroker()
        received = []

        async def consume():
            async for event in broker.subscribe():
                received.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        broker.publish("test", {"msg": "hello"})
        await asyncio.sleep(0.01)

        broker.disconnect_all()
        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].event == "test"
        assert received[0].data == {"msg": "hello"}

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        broker = SSEBroker()
        received_a: list[SSEEvent] = []
        received_b: list[SSEEvent] = []

        async def consume(target: list):
            async for event in broker.subscribe():
                target.append(event)

        task_a = asyncio.create_task(consume(received_a))
        task_b = asyncio.create_task(consume(received_b))
        await asyncio.sleep(0.01)

        broker.publish("ping", {"n": 1})
        await asyncio.sleep(0.01)

        broker.disconnect_all()
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].data == {"n": 1}

    @pytest.mark.asyncio
    async def test_event_order_preserved(self):
        broker = SSEBroker()
        received: list[SSEEvent] = []

        async def consume():
            async for event in broker.subscribe():
                received.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        for i in range(5):
            broker.publish("seq", {"i": i})
        await asyncio.sleep(0.01)

        broker.disconnect_all()
        await asyncio.wait_for(task, timeout=1.0)

        assert [e.data["i"] for e in received] == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_no_subscribers_no_error(self):
        broker = SSEBroker()
        broker.publish("lonely", {"x": 1})
        # No crash expected

    @pytest.mark.asyncio
    async def test_disconnect_all_terminates(self):
        broker = SSEBroker()
        finished = asyncio.Event()

        async def consume():
            async for _ in broker.subscribe():
                pass
            finished.set()

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        broker.disconnect_all()
        await asyncio.wait_for(finished.wait(), timeout=1.0)
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_disconnect_all_clears_queues(self):
        broker = SSEBroker()

        async def consume():
            async for _ in broker.subscribe():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        assert len(broker._queues) == 1

        broker.disconnect_all()
        await asyncio.wait_for(task, timeout=1.0)

        assert len(broker._queues) == 0
