"""
Background heartbeat loop — periodic observe-reason-act tick.

Phase 4C: logs heartbeat tick to tracing.
Phase 4C-Part-2: will check scheduled jobs and trigger proactive actions.

Watches: polls Ollama version; notifies via Redis when Ollama is updated so
the owner knows to retry pulling models that required a newer version.
"""

import asyncio
import json
import os

import requests
import tracing

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "60"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
WATCH_MODEL = os.getenv("WATCH_MODEL", "qwen3.5:35b-a3b")

_VERSION_KEY = "heartbeat:ollama_version"
_NOTIFIED_KEY = "heartbeat:ollama_update_notified"


async def heartbeat_loop(state) -> None:
    """Main heartbeat loop — runs forever, catching all exceptions per tick."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await _tick(state)
        except Exception as e:
            tracing._emit("heartbeat", {"status": "error", "error": str(e)})


async def _tick(state) -> None:
    """Single heartbeat tick."""
    tracing._emit("heartbeat", {"status": "tick"})
    await _check_ollama_version(state)


async def _check_ollama_version(state) -> None:
    """Check Ollama version; publish a notification to Redis if it has updated."""
    redis = getattr(state, "redis_client", None)
    if redis is None:
        return

    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/version", timeout=5)
        resp.raise_for_status()
        current_version = resp.json().get("version", "unknown")
    except Exception:
        return  # Ollama unreachable — skip silently

    last_version = redis.get(_VERSION_KEY)

    if last_version is None:
        # First run — store version, nothing to compare yet
        redis.set(_VERSION_KEY, current_version)
        return

    if current_version == last_version:
        return  # No change

    # Version changed — Ollama was updated
    redis.set(_VERSION_KEY, current_version)
    redis.delete(_NOTIFIED_KEY)  # Reset so we notify again on next update

    message = (
        f"🆕 *Ollama updated!* `{last_version}` → `{current_version}`\n\n"
        f"You can now retry pulling `{WATCH_MODEL}`:\n"
        f"`docker exec ollama-runner ollama pull {WATCH_MODEL}`"
    )
    redis.publish("notifications:agent", json.dumps({"text": message}))
    tracing._emit("heartbeat", {
        "status": "ollama_updated",
        "from": last_version,
        "to": current_version,
    })


def start_heartbeat(state) -> asyncio.Task:
    """Start the heartbeat loop as a background asyncio task."""
    return asyncio.create_task(heartbeat_loop(state))
