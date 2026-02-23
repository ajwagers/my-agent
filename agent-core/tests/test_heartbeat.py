"""
Tests for heartbeat.py — background observe-reason-act loop.

All tests run without Docker, real Redis, or network access.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_tick_invokes_tracing(self):
        """_tick() emits a heartbeat event to tracing."""
        from heartbeat import _tick
        with patch("heartbeat.tracing") as mock_tracing:
            await _tick(MagicMock())
        mock_tracing._emit.assert_called_once()
        event_type = mock_tracing._emit.call_args[0][0]
        assert event_type == "heartbeat"

    @pytest.mark.asyncio
    async def test_exception_in_tick_is_caught_loop_continues(self):
        """Exception inside _tick() is caught by the loop — does not crash it."""
        from heartbeat import heartbeat_loop

        tick_calls = []

        async def bad_tick(state):
            tick_calls.append(1)
            raise RuntimeError("tick failed")

        with patch("heartbeat._tick", bad_tick), \
             patch("heartbeat.HEARTBEAT_INTERVAL", 0), \
             patch("heartbeat.tracing"):
            task = asyncio.create_task(heartbeat_loop(MagicMock()))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(tick_calls) >= 1

    @pytest.mark.asyncio
    async def test_start_heartbeat_returns_task(self):
        """start_heartbeat() returns an asyncio.Task."""
        from heartbeat import start_heartbeat
        with patch("heartbeat.HEARTBEAT_INTERVAL", 9999):
            task = start_heartbeat(MagicMock())
        assert isinstance(task, asyncio.Task)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_task_cancellation_raises_cancelled_error(self):
        """Task cancellation is not swallowed — CancelledError propagates to awaiter."""
        from heartbeat import start_heartbeat
        with patch("heartbeat.HEARTBEAT_INTERVAL", 9999):
            task = start_heartbeat(MagicMock())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
