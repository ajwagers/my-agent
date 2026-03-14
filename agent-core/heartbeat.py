"""
Background heartbeat loop — periodic observe-reason-act tick.

Phase 4C: logs heartbeat tick to tracing.
Phase 4C-Part-2: checks scheduled jobs and triggers proactive actions.

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
    await _process_due_jobs(state)


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


async def _process_due_jobs(state) -> None:
    """Check for due jobs and fire each as a background task."""
    job_manager = getattr(state, "job_manager", None)
    if job_manager is None:
        return
    due = job_manager.get_due_jobs()
    for job in due:
        if not job_manager.mark_running(job["id"]):
            continue  # locked — another tick is processing it
        asyncio.create_task(_run_job(state, job))


async def _run_job(state, job) -> None:
    """Execute a single job through the full tool loop, then notify the owner."""
    from skill_runner import run_tool_loop

    job_id = job["id"]
    notify: str
    try:
        messages = [{"role": "user", "content": job["prompt"]}]
        final_text, _, _ = await run_tool_loop(
            ollama_client=state.ollama_client,
            messages=messages,
            tools=state.skill_registry.to_ollama_tools() or None,
            model=state.tool_model,
            ctx=state.num_ctx,
            skill_registry=state.skill_registry,
            policy_engine=state.policy_engine,
            approval_manager=state.approval_manager,
            auto_approve=False,
            user_id=job["user_id"],
            max_iterations=state.max_tool_iterations,
        )
        state.job_manager.mark_complete(job_id, final_text[:200])
        if job["job_type"] == "recurring":
            state.job_manager.reschedule(job_id)
        notify = f"✅ Job done: {job['prompt'][:60]}\n\n{final_text[:500]}"
        tracing.log_job_event(job_id, "completed", user_id=job["user_id"])
    except Exception as e:
        state.job_manager.mark_failed(job_id, str(e))
        notify = f"❌ Job failed: {job['prompt'][:60]}\nError: {e}"
        tracing.log_job_event(job_id, "failed", user_id=job["user_id"], error=str(e))
    finally:
        state.job_manager.release_lock(job_id)

    try:
        state.redis_client.publish("notifications:agent", notify)
    except Exception:
        pass


def start_heartbeat(state) -> asyncio.Task:
    """Start the heartbeat loop as a background asyncio task."""
    return asyncio.create_task(heartbeat_loop(state))
