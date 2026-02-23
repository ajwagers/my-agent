"""
Background heartbeat loop — periodic observe-reason-act tick.

Phase 4C: logs heartbeat tick to tracing.
Phase 4C-Part-2: will check scheduled jobs and trigger proactive actions.
"""

import asyncio
import os

import tracing

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "60"))


async def heartbeat_loop(state) -> None:
    """Main heartbeat loop — runs forever, catching all exceptions per tick."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await _tick(state)
        except Exception as e:
            tracing._emit("heartbeat", {"status": "error", "error": str(e)})


async def _tick(state) -> None:
    """Single heartbeat tick.

    Phase 4C: emit heartbeat event to tracing.
    Phase 4C-Part-2: check scheduled jobs, trigger proactive actions.
    """
    tracing._emit("heartbeat", {"status": "tick"})


def start_heartbeat(state) -> asyncio.Task:
    """Start the heartbeat loop as a background asyncio task."""
    return asyncio.create_task(heartbeat_loop(state))
