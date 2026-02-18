"""
Tests for structured tracing & observability.
Runnable without Docker: python -m pytest tests/test_tracing.py -v
"""

import json
import logging
import time

import pytest

import tracing
from tracing import (
    JSONFormatter,
    _sanitize,
    _truncate,
    get_recent_logs,
    get_trace_id,
    log_approval_event,
    log_chat_request,
    log_chat_response,
    log_policy_decision,
    log_skill_call,
    new_trace,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_tracing(fake_redis):
    """Reset tracing module state before each test."""
    tracing._redis_client = None
    tracing._logger = None
    # Reset context vars
    tracing._trace_id.set("")
    tracing._user_id.set("")
    tracing._channel.set("")
    yield


@pytest.fixture
def traced_redis(fake_redis):
    """Set up tracing with FakeRedis and return the redis instance."""
    setup_logging(redis_client=fake_redis)
    return fake_redis


# ============================================================
# Trace context tests
# ============================================================
class TestTraceContext:

    def test_new_trace_returns_16_char_hex(self):
        tid = new_trace()
        assert len(tid) == 16
        int(tid, 16)  # Should not raise — valid hex

    def test_new_trace_sets_context(self):
        tid = new_trace(user_id="alice", channel="telegram")
        assert get_trace_id() == tid
        ctx = tracing.get_trace_context()
        assert ctx["user_id"] == "alice"
        assert ctx["channel"] == "telegram"

    def test_successive_traces_differ(self):
        t1 = new_trace()
        t2 = new_trace()
        assert t1 != t2

    def test_get_trace_id_empty_before_new_trace(self):
        assert get_trace_id() == ""

    def test_trace_context_defaults(self):
        ctx = tracing.get_trace_context()
        assert ctx["trace_id"] == ""
        assert ctx["user_id"] == ""
        assert ctx["channel"] == ""


# ============================================================
# JSON Formatter tests
# ============================================================
class TestJSONFormatter:

    def test_format_produces_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_format_includes_structured_data(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="event", args=(), exc_info=None,
        )
        record.structured_data = {"event_type": "chat", "model": "phi3"}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event_type"] == "chat"
        assert parsed["model"] == "phi3"

    def test_format_single_line(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="test", args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "\n" not in output


# ============================================================
# Chat logging tests
# ============================================================
class TestChatLogging:

    def test_log_chat_request_fields(self, traced_redis):
        new_trace(user_id="bob", channel="slack")
        result = log_chat_request("Hello world", model="phi3:latest")
        entry = json.loads(result)
        assert entry["event_type"] == "chat"
        assert entry["model"] == "phi3:latest"
        assert entry["user_id"] == "bob"
        assert entry["channel"] == "slack"
        assert "message_preview" in entry
        assert "timestamp" in entry
        assert "trace_id" in entry

    def test_log_chat_request_truncates_message(self, traced_redis):
        new_trace()
        long_msg = "x" * 300
        result = log_chat_request(long_msg, model="phi3")
        entry = json.loads(result)
        assert len(entry["message_preview"]) <= 104  # 100 + "..."

    def test_log_chat_response_metrics(self, traced_redis):
        new_trace()
        result = log_chat_response(
            model="phi3",
            response_preview="Sure, I can help",
            eval_count=42,
            prompt_eval_count=10,
            total_duration_ms=1234.567,
        )
        entry = json.loads(result)
        assert entry["metrics"]["eval_count"] == 42
        assert entry["metrics"]["prompt_eval_count"] == 10
        assert entry["metrics"]["total_duration_ms"] == 1234.57

    def test_log_chat_request_stored_in_redis(self, traced_redis):
        new_trace()
        log_chat_request("test message", model="phi3")
        entries = traced_redis.lrange("logs:chat", 0, -1)
        assert len(entries) == 1
        parsed = json.loads(entries[0])
        assert parsed["event_type"] == "chat"

    def test_log_chat_request_in_firehose(self, traced_redis):
        new_trace()
        log_chat_request("test", model="phi3")
        entries = traced_redis.lrange("logs:all", 0, -1)
        assert len(entries) == 1

    def test_extra_kwargs_included(self, traced_redis):
        new_trace()
        result = log_chat_request("hi", model="phi3", bootstrap=True)
        entry = json.loads(result)
        assert entry["bootstrap"] is True


# ============================================================
# Shared trace ID tests
# ============================================================
class TestSharedTraceID:

    def test_chat_and_skill_share_trace_id(self, traced_redis):
        tid = new_trace(user_id="alice")
        r1 = log_chat_request("do something", model="phi3")
        r2 = log_skill_call("web_search", params={"query": "test"})
        e1 = json.loads(r1)
        e2 = json.loads(r2)
        assert e1["trace_id"] == e2["trace_id"] == tid

    def test_chat_and_policy_share_trace_id(self, traced_redis):
        tid = new_trace()
        r1 = log_chat_request("msg", model="phi3")
        r2 = log_policy_decision(action="write", zone="identity", decision="allow")
        e1 = json.loads(r1)
        e2 = json.loads(r2)
        assert e1["trace_id"] == e2["trace_id"] == tid

    def test_different_traces_different_ids(self, traced_redis):
        t1 = new_trace()
        r1 = log_chat_request("first", model="phi3")
        t2 = new_trace()
        r2 = log_chat_request("second", model="phi3")
        e1 = json.loads(r1)
        e2 = json.loads(r2)
        assert e1["trace_id"] != e2["trace_id"]


# ============================================================
# Skill logging tests
# ============================================================
class TestSkillLogging:

    def test_log_skill_call_fields(self, traced_redis):
        new_trace()
        result = log_skill_call("web_search", params={"query": "weather"})
        entry = json.loads(result)
        assert entry["event_type"] == "skill"
        assert entry["skill_name"] == "web_search"
        assert entry["params"]["query"] == "weather"

    def test_skill_stored_in_type_list(self, traced_redis):
        new_trace()
        log_skill_call("calculator")
        entries = traced_redis.lrange("logs:skill", 0, -1)
        assert len(entries) == 1


# ============================================================
# Policy logging tests
# ============================================================
class TestPolicyLogging:

    def test_log_policy_decision_fields(self, traced_redis):
        new_trace()
        result = log_policy_decision(
            action="write",
            zone="identity",
            decision="requires_approval",
            risk_level="medium",
            reason="identity zone write",
        )
        entry = json.loads(result)
        assert entry["event_type"] == "policy"
        assert entry["action"] == "write"
        assert entry["zone"] == "identity"
        assert entry["decision"] == "requires_approval"
        assert entry["risk_level"] == "medium"

    def test_policy_stored_in_type_list(self, traced_redis):
        new_trace()
        log_policy_decision(action="read", decision="allow")
        entries = traced_redis.lrange("logs:policy", 0, -1)
        assert len(entries) == 1

    def test_policy_reason_truncated(self, traced_redis):
        new_trace()
        result = log_policy_decision(
            action="write", reason="x" * 500,
        )
        entry = json.loads(result)
        assert len(entry["reason"]) <= 204  # 200 + "..."


# ============================================================
# Approval logging tests
# ============================================================
class TestApprovalLogging:

    def test_log_approval_requested(self, traced_redis):
        new_trace()
        result = log_approval_event(
            approval_id="abc-123",
            action="requested",
            zone="identity",
            risk_level="medium",
            status="pending",
            description="Write soul.md",
        )
        entry = json.loads(result)
        assert entry["event_type"] == "approval"
        assert entry["approval_id"] == "abc-123"
        assert entry["action"] == "requested"
        assert entry["status"] == "pending"

    def test_log_approval_resolved_with_response_time(self, traced_redis):
        new_trace()
        result = log_approval_event(
            approval_id="abc-123",
            action="approved",
            zone="identity",
            risk_level="medium",
            status="approved",
            description="Write soul.md",
            response_time_ms=1523.456,
        )
        entry = json.loads(result)
        assert entry["response_time_ms"] == 1523.46

    def test_approval_stored_in_type_list(self, traced_redis):
        new_trace()
        log_approval_event(approval_id="x", action="requested", status="pending")
        entries = traced_redis.lrange("logs:approval", 0, -1)
        assert len(entries) == 1

    def test_approval_no_response_time_when_zero(self, traced_redis):
        new_trace()
        result = log_approval_event(
            approval_id="x", action="requested", status="pending",
        )
        entry = json.loads(result)
        assert "response_time_ms" not in entry


# ============================================================
# Redis queryable tests
# ============================================================
class TestRedisQueryable:

    def test_get_recent_logs_returns_entries(self, traced_redis):
        new_trace()
        log_chat_request("msg1", model="phi3")
        log_chat_request("msg2", model="phi3")
        results = get_recent_logs(traced_redis, "chat", count=10)
        assert len(results) == 2
        # Newest first (lpush)
        assert results[0]["message_preview"] == "msg2"

    def test_get_recent_logs_with_offset(self, traced_redis):
        new_trace()
        for i in range(5):
            log_chat_request(f"msg{i}", model="phi3")
        results = get_recent_logs(traced_redis, "chat", count=2, offset=2)
        assert len(results) == 2
        # Skip 2 newest, get next 2
        assert results[0]["message_preview"] == "msg2"
        assert results[1]["message_preview"] == "msg1"

    def test_get_recent_logs_firehose(self, traced_redis):
        new_trace()
        log_chat_request("chat", model="phi3")
        log_skill_call("search")
        results = get_recent_logs(traced_redis, "all", count=10)
        assert len(results) == 2

    def test_get_recent_logs_none_redis(self):
        results = get_recent_logs(None, "all")
        assert results == []

    def test_get_recent_logs_empty(self, traced_redis):
        results = get_recent_logs(traced_redis, "chat")
        assert results == []


# ============================================================
# Retention tests
# ============================================================
class TestRetention:

    def test_all_log_trimmed_to_limit(self, traced_redis):
        new_trace()
        # Push more than ALL_LOG_LIMIT entries
        for i in range(tracing.ALL_LOG_LIMIT + 50):
            log_chat_request(f"msg{i}", model="phi3")
        assert traced_redis.llen("logs:all") == tracing.ALL_LOG_LIMIT

    def test_type_log_trimmed_to_limit(self, traced_redis):
        new_trace()
        for i in range(tracing.TYPE_LOG_LIMIT + 50):
            log_skill_call(f"skill_{i}")
        assert traced_redis.llen("logs:skill") == tracing.TYPE_LOG_LIMIT

    def test_firehose_and_type_independent(self, traced_redis):
        new_trace()
        # Chat goes to logs:all AND logs:chat
        for i in range(100):
            log_chat_request(f"msg{i}", model="phi3")
        assert traced_redis.llen("logs:all") == 100
        assert traced_redis.llen("logs:chat") == 100


# ============================================================
# Redis resilience tests
# ============================================================
class TestRedisResilience:

    def test_logging_works_without_redis(self):
        """With redis_client=None, logging should still work (stdout only)."""
        setup_logging(redis_client=None)
        new_trace(user_id="test")
        # Should not raise
        result = log_chat_request("hello", model="phi3")
        entry = json.loads(result)
        assert entry["event_type"] == "chat"

    def test_logging_survives_redis_error(self):
        """If Redis raises, the log entry is still returned."""
        class BrokenRedis:
            def lpush(self, *args, **kwargs):
                raise ConnectionError("Redis down")
            def ltrim(self, *args, **kwargs):
                raise ConnectionError("Redis down")

        setup_logging(redis_client=BrokenRedis())
        new_trace()
        # Should not raise
        result = log_chat_request("test", model="phi3")
        entry = json.loads(result)
        assert entry["event_type"] == "chat"

    def test_setup_logging_returns_logger(self, fake_redis):
        logger = setup_logging(redis_client=fake_redis)
        assert isinstance(logger, logging.Logger)
        assert logger.name == "agent.tracing"

    def test_setup_logging_no_duplicate_handlers(self, fake_redis):
        """Calling setup_logging twice should not add duplicate handlers."""
        setup_logging(redis_client=fake_redis)
        setup_logging(redis_client=fake_redis)
        logger = logging.getLogger("agent.tracing")
        assert len(logger.handlers) == 1


# ============================================================
# Sanitization tests
# ============================================================
class TestSanitization:

    def test_redact_password(self):
        result = _sanitize({"password": "s3cret", "name": "alice"})
        assert result["password"] == "***REDACTED***"
        assert result["name"] == "alice"

    def test_redact_token(self):
        result = _sanitize({"token": "abc123"})
        assert result["token"] == "***REDACTED***"

    def test_redact_api_key(self):
        result = _sanitize({"api_key": "key123"})
        assert result["api_key"] == "***REDACTED***"

    def test_redact_secret(self):
        result = _sanitize({"secret": "value"})
        assert result["secret"] == "***REDACTED***"

    def test_redact_nested(self):
        result = _sanitize({"config": {"password": "hidden", "host": "localhost"}})
        assert result["config"]["password"] == "***REDACTED***"
        assert result["config"]["host"] == "localhost"

    def test_case_insensitive_redaction(self):
        result = _sanitize({"Password": "hidden", "TOKEN": "hidden"})
        assert result["Password"] == "***REDACTED***"
        assert result["TOKEN"] == "***REDACTED***"

    def test_empty_params(self):
        assert _sanitize({}) == {}
        assert _sanitize(None) is None

    def test_skill_params_sanitized(self, traced_redis):
        new_trace()
        result = log_skill_call("api_call", params={"url": "http://x", "token": "secret"})
        entry = json.loads(result)
        assert entry["params"]["token"] == "***REDACTED***"
        assert entry["params"]["url"] == "http://x"

    def test_truncate_long_string(self):
        assert _truncate("x" * 300) == "x" * 200 + "..."

    def test_truncate_short_string(self):
        assert _truncate("hello") == "hello"

    def test_truncate_non_string(self):
        assert _truncate(42) == 42
        assert _truncate(None) is None
