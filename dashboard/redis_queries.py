"""
Redis data access layer for the Health Dashboard.

Queries the same Redis lists that agent-core/tracing.py writes to.
Replicates the query pattern independently (no cross-container imports).

Redis key structure (written by tracing.py):
  logs:all        — firehose (last 1000 entries)
  logs:chat       — chat events (last 500)
  logs:skill      — skill invocations (last 500)
  logs:policy     — policy decisions (last 500)
  logs:approval   — approval gate events (last 500)

Approval hashes (written by approval.py):
  approval:{uuid} — hash with id, action, zone, risk_level, status, etc.
"""

import json
import time

LOG_TYPES = ("all", "chat", "skill", "policy", "approval")


def get_recent_logs(redis_client, log_type="all", count=50, offset=0):
    """Retrieve recent log entries from Redis, newest first.

    Mirrors tracing.get_recent_logs() from agent-core.
    """
    if redis_client is None:
        return []
    key = f"logs:{log_type}"
    try:
        raw = redis_client.lrange(key, offset, offset + count - 1)
        return [json.loads(entry) for entry in raw]
    except Exception:
        return []


def count_logs_by_type(redis_client):
    """Return {log_type: count} for all log lists."""
    counts = {}
    if redis_client is None:
        return counts
    for t in LOG_TYPES:
        try:
            counts[t] = redis_client.llen(f"logs:{t}")
        except Exception:
            counts[t] = 0
    return counts


def get_activity_stats(redis_client, hours=24):
    """Aggregate activity metrics from the logs:all firehose.

    Reads up to 1000 entries (the retention cap) and filters by timestamp.
    Returns a dict with:
      total_requests, requests_this_hour, requests_by_channel,
      skill_counts, avg_response_time_by_model, policy_decisions
    """
    stats = {
        "total_requests": 0,
        "requests_this_hour": 0,
        "requests_by_channel": {},
        "skill_counts": {},
        "avg_response_time_by_model": {},
        "policy_decisions": {"allow": 0, "deny": 0, "requires_approval": 0},
    }
    if redis_client is None:
        return stats

    now = time.time()
    cutoff = now - (hours * 3600)
    hour_cutoff = now - 3600

    entries = get_recent_logs(redis_client, "all", count=1000)

    # Accumulators for response-time averaging
    model_durations = {}  # model -> [duration_ms, ...]

    for entry in entries:
        ts = entry.get("timestamp", 0)
        if ts < cutoff:
            continue

        event_type = entry.get("event_type", "")

        if event_type == "chat":
            # Chat requests have message_preview, responses have response_preview
            if "message_preview" in entry:
                stats["total_requests"] += 1
                if ts >= hour_cutoff:
                    stats["requests_this_hour"] += 1
                channel = entry.get("channel", "unknown") or "unknown"
                stats["requests_by_channel"][channel] = (
                    stats["requests_by_channel"].get(channel, 0) + 1
                )
            if "metrics" in entry:
                model = entry.get("model", "unknown")
                ms = entry["metrics"].get("total_duration_ms", 0)
                if ms > 0:
                    model_durations.setdefault(model, []).append(ms)

        elif event_type == "skill":
            name = entry.get("skill_name", "unknown")
            stats["skill_counts"][name] = stats["skill_counts"].get(name, 0) + 1

        elif event_type == "policy":
            decision = entry.get("decision", "")
            if decision in stats["policy_decisions"]:
                stats["policy_decisions"][decision] += 1

    # Compute averages
    for model, durations in model_durations.items():
        stats["avg_response_time_by_model"][model] = sum(durations) / len(durations)

    return stats


def get_pending_approvals(redis_client):
    """Return list of pending approval request dicts.

    Scans approval:* hash keys and filters by status=="pending".
    Mirrors ApprovalManager.get_pending() from agent-core/approval.py.
    """
    pending = []
    if redis_client is None:
        return pending
    try:
        keys = redis_client.keys("approval:*")
        for key in keys:
            data = redis_client.hgetall(key)
            if data and data.get("status") == "pending":
                pending.append(data)
    except Exception:
        pass
    return pending


def get_approval_history(redis_client, count=50):
    """Return recent approval events from logs:approval."""
    return get_recent_logs(redis_client, "approval", count=count)


def get_security_events(redis_client, count=50):
    """Return denied policy decisions and failed/timed-out approvals.

    Combines policy denials with approval timeouts/denials, sorted newest first.
    """
    events = []
    if redis_client is None:
        return events

    # Policy denials
    policy_logs = get_recent_logs(redis_client, "policy", count=500)
    for entry in policy_logs:
        if entry.get("decision") in ("deny", "requires_approval"):
            events.append(entry)

    # Approval denials and timeouts
    approval_logs = get_recent_logs(redis_client, "approval", count=500)
    for entry in approval_logs:
        if entry.get("status") in ("denied", "timeout"):
            events.append(entry)

    # Sort by timestamp descending, return top N
    events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    return events[:count]
