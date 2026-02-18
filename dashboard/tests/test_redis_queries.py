"""Tests for dashboard/redis_queries.py."""

import json
import time

import pytest

from redis_queries import (
    get_recent_logs,
    count_logs_by_type,
    get_activity_stats,
    get_pending_approvals,
    get_approval_history,
    get_security_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _push_log(redis_client, log_type, entry):
    """Push a JSON log entry to the appropriate Redis lists."""
    entry.setdefault("event_type", log_type)
    entry.setdefault("timestamp", time.time())
    blob = json.dumps(entry)
    redis_client.lpush(f"logs:{log_type}", blob)
    redis_client.lpush("logs:all", blob)


# ---------------------------------------------------------------------------
# get_recent_logs
# ---------------------------------------------------------------------------
class TestGetRecentLogs:
    def test_returns_parsed_dicts(self, fake_redis):
        _push_log(fake_redis, "chat", {"model": "phi3"})
        result = get_recent_logs(fake_redis, "chat", count=10)
        assert len(result) == 1
        assert result[0]["model"] == "phi3"

    def test_empty_list_returns_empty(self, fake_redis):
        assert get_recent_logs(fake_redis, "chat") == []

    def test_none_client_returns_empty(self):
        assert get_recent_logs(None) == []

    def test_respects_count(self, fake_redis):
        for i in range(10):
            _push_log(fake_redis, "chat", {"i": i})
        result = get_recent_logs(fake_redis, "chat", count=3)
        assert len(result) == 3

    def test_newest_first(self, fake_redis):
        _push_log(fake_redis, "chat", {"order": "first", "timestamp": 1.0})
        _push_log(fake_redis, "chat", {"order": "second", "timestamp": 2.0})
        result = get_recent_logs(fake_redis, "chat", count=10)
        assert result[0]["order"] == "second"
        assert result[1]["order"] == "first"


# ---------------------------------------------------------------------------
# count_logs_by_type
# ---------------------------------------------------------------------------
class TestCountLogsByType:
    def test_counts_each_type(self, fake_redis):
        _push_log(fake_redis, "chat", {})
        _push_log(fake_redis, "chat", {})
        _push_log(fake_redis, "policy", {})
        counts = count_logs_by_type(fake_redis)
        assert counts["chat"] == 2
        assert counts["policy"] == 1
        assert counts["skill"] == 0
        assert counts["all"] == 3

    def test_none_client(self):
        assert count_logs_by_type(None) == {}


# ---------------------------------------------------------------------------
# get_activity_stats
# ---------------------------------------------------------------------------
class TestGetActivityStats:
    def test_counts_chat_requests_by_channel(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "chat", {
            "message_preview": "hello",
            "channel": "telegram",
            "timestamp": now,
        })
        _push_log(fake_redis, "chat", {
            "message_preview": "hi",
            "channel": "telegram",
            "timestamp": now,
        })
        _push_log(fake_redis, "chat", {
            "message_preview": "hey",
            "channel": "cli",
            "timestamp": now,
        })
        stats = get_activity_stats(fake_redis, hours=1)
        assert stats["total_requests"] == 3
        assert stats["requests_by_channel"]["telegram"] == 2
        assert stats["requests_by_channel"]["cli"] == 1

    def test_excludes_old_entries(self, fake_redis):
        old_ts = time.time() - 7200  # 2 hours ago
        _push_log(fake_redis, "chat", {
            "message_preview": "old",
            "channel": "cli",
            "timestamp": old_ts,
        })
        stats = get_activity_stats(fake_redis, hours=1)
        assert stats["total_requests"] == 0

    def test_avg_response_time_by_model(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "chat", {
            "model": "phi3",
            "response_preview": "hi",
            "metrics": {"total_duration_ms": 100, "eval_count": 10, "prompt_eval_count": 5},
            "timestamp": now,
        })
        _push_log(fake_redis, "chat", {
            "model": "phi3",
            "response_preview": "hey",
            "metrics": {"total_duration_ms": 200, "eval_count": 20, "prompt_eval_count": 10},
            "timestamp": now,
        })
        stats = get_activity_stats(fake_redis, hours=1)
        assert stats["avg_response_time_by_model"]["phi3"] == 150.0

    def test_skill_counts(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "skill", {"skill_name": "web_search", "timestamp": now})
        _push_log(fake_redis, "skill", {"skill_name": "web_search", "timestamp": now})
        _push_log(fake_redis, "skill", {"skill_name": "file_read", "timestamp": now})
        stats = get_activity_stats(fake_redis, hours=1)
        assert stats["skill_counts"]["web_search"] == 2
        assert stats["skill_counts"]["file_read"] == 1

    def test_policy_decisions(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "policy", {"decision": "allow", "timestamp": now})
        _push_log(fake_redis, "policy", {"decision": "deny", "timestamp": now})
        _push_log(fake_redis, "policy", {"decision": "deny", "timestamp": now})
        stats = get_activity_stats(fake_redis, hours=1)
        assert stats["policy_decisions"]["allow"] == 1
        assert stats["policy_decisions"]["deny"] == 2

    def test_requests_this_hour(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "chat", {
            "message_preview": "recent",
            "channel": "cli",
            "timestamp": now,
        })
        _push_log(fake_redis, "chat", {
            "message_preview": "older",
            "channel": "cli",
            "timestamp": now - 7200,  # 2 hours ago
        })
        stats = get_activity_stats(fake_redis, hours=24)
        assert stats["total_requests"] == 2
        assert stats["requests_this_hour"] == 1

    def test_none_client(self):
        stats = get_activity_stats(None)
        assert stats["total_requests"] == 0


# ---------------------------------------------------------------------------
# get_pending_approvals
# ---------------------------------------------------------------------------
class TestGetPendingApprovals:
    def test_returns_pending_only(self, fake_redis):
        fake_redis.hset("approval:aaa", mapping={"id": "aaa", "status": "pending", "action": "write"})
        fake_redis.hset("approval:bbb", mapping={"id": "bbb", "status": "approved", "action": "write"})
        fake_redis.hset("approval:ccc", mapping={"id": "ccc", "status": "pending", "action": "delete"})
        pending = get_pending_approvals(fake_redis)
        assert len(pending) == 2
        ids = {p["id"] for p in pending}
        assert ids == {"aaa", "ccc"}

    def test_empty(self, fake_redis):
        assert get_pending_approvals(fake_redis) == []

    def test_none_client(self):
        assert get_pending_approvals(None) == []


# ---------------------------------------------------------------------------
# get_security_events
# ---------------------------------------------------------------------------
class TestGetSecurityEvents:
    def test_includes_policy_denials(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "policy", {
            "decision": "deny", "action": "shell", "zone": "system", "timestamp": now,
        })
        _push_log(fake_redis, "policy", {
            "decision": "allow", "action": "read", "zone": "sandbox", "timestamp": now,
        })
        events = get_security_events(fake_redis)
        assert len(events) == 1
        assert events[0]["decision"] == "deny"

    def test_includes_approval_timeouts(self, fake_redis):
        now = time.time()
        _push_log(fake_redis, "approval", {
            "status": "timeout", "action": "write", "timestamp": now,
        })
        _push_log(fake_redis, "approval", {
            "status": "approved", "action": "write", "timestamp": now,
        })
        events = get_security_events(fake_redis)
        assert len(events) == 1
        assert events[0]["status"] == "timeout"

    def test_sorted_newest_first(self, fake_redis):
        _push_log(fake_redis, "policy", {
            "decision": "deny", "action": "old", "timestamp": 1.0,
        })
        _push_log(fake_redis, "approval", {
            "status": "denied", "action": "new", "timestamp": 2.0,
        })
        events = get_security_events(fake_redis)
        assert events[0]["action"] == "new"
        assert events[1]["action"] == "old"
