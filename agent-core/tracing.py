"""
Structured tracing & observability for the agent stack.

Provides JSON-formatted logging to stdout (Docker captures) and Redis lists
(dashboard reads). Every request gets a trace ID via contextvars so all
downstream calls share the same correlation ID.

Redis key structure:
  logs:all           — firehose (last 1000 entries)
  logs:<event_type>  — type-specific (last 500 each): chat, skill, policy, approval
"""

import contextvars
import json
import logging
import time
import uuid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Context variables — set once at request entry, read everywhere downstream
# ---------------------------------------------------------------------------
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")
_channel: contextvars.ContextVar[str] = contextvars.ContextVar("channel", default="")

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_redis_client = None
_logger: Optional[logging.Logger] = None

# Retention limits
ALL_LOG_LIMIT = 1000
TYPE_LOG_LIMIT = 500

# Sensitive key patterns to redact
_SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "apikey", "api_secret"}

# Max length for truncated fields
_MAX_FIELD_LEN = 200


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON for stdout."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Merge any extra structured data
        if hasattr(record, "structured_data"):
            entry.update(record.structured_data)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging(redis_client=None) -> logging.Logger:
    """Configure the structured logger. Call once at startup.

    Args:
        redis_client: Redis connection for log storage. None = stdout only.

    Returns:
        Configured logger instance.
    """
    global _redis_client, _logger

    _redis_client = redis_client

    logger = logging.getLogger("agent.tracing")
    # Avoid duplicate handlers on repeated calls
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    _logger = logger
    return logger


def _get_logger() -> logging.Logger:
    """Return the tracing logger, initializing if needed."""
    global _logger
    if _logger is None:
        return setup_logging()
    return _logger


# ---------------------------------------------------------------------------
# Trace context management
# ---------------------------------------------------------------------------
def new_trace(user_id: str = "", channel: str = "") -> str:
    """Start a new trace. Returns the generated trace ID (16-char hex)."""
    trace_id = uuid.uuid4().hex[:16]
    _trace_id.set(trace_id)
    _user_id.set(user_id)
    _channel.set(channel)
    return trace_id


def get_trace_id() -> str:
    """Return the current trace ID, or empty string if none set."""
    return _trace_id.get()


def get_trace_context() -> dict:
    """Return current trace context as a dict."""
    return {
        "trace_id": _trace_id.get(),
        "user_id": _user_id.get(),
        "channel": _channel.get(),
    }


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------
def _sanitize(params: dict) -> dict:
    """Redact sensitive keys from a parameter dict (shallow copy)."""
    if not params:
        return params
    result = {}
    for k, v in params.items():
        if k.lower() in _SENSITIVE_KEYS:
            result[k] = "***REDACTED***"
        elif isinstance(v, dict):
            result[k] = _sanitize(v)
        else:
            result[k] = v
    return result


def _truncate(value: Any, max_len: int = _MAX_FIELD_LEN) -> Any:
    """Truncate string values beyond max_len, preserving non-strings."""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + "..."
    return value


# ---------------------------------------------------------------------------
# Internal emit + Redis push
# ---------------------------------------------------------------------------
def _emit(event_type: str, data: dict) -> str:
    """Log a structured event to stdout and Redis.

    Returns the JSON string that was emitted.
    """
    logger = _get_logger()

    entry = {
        "event_type": event_type,
        "timestamp": time.time(),
    }
    entry.update(get_trace_context())
    entry.update(data)

    json_str = json.dumps(entry, default=str)

    # Log to stdout via the JSON formatter
    record = logging.LogRecord(
        name="agent.tracing",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=json_str,
        args=(),
        exc_info=None,
    )
    record.structured_data = entry
    logger.handle(record)

    # Push to Redis
    _push_to_redis(event_type, json_str)

    return json_str


def _push_to_redis(event_type: str, json_str: str) -> None:
    """Push log entry to Redis lists with retention trimming."""
    if _redis_client is None:
        return
    try:
        # Firehose list
        _redis_client.lpush("logs:all", json_str)
        _redis_client.ltrim("logs:all", 0, ALL_LOG_LIMIT - 1)

        # Type-specific list
        type_key = f"logs:{event_type}"
        _redis_client.lpush(type_key, json_str)
        _redis_client.ltrim(type_key, 0, TYPE_LOG_LIMIT - 1)
    except Exception:
        pass  # Never crash a request due to logging


# ---------------------------------------------------------------------------
# Public event emitters
# ---------------------------------------------------------------------------
def log_chat_request(message: str, model: str, **extra) -> str:
    """Log an incoming chat request."""
    data = {
        "model": model,
        "message_preview": _truncate(message, 100),
    }
    data.update(extra)
    return _emit("chat", data)


def log_chat_response(
    model: str,
    response_preview: str = "",
    eval_count: int = 0,
    prompt_eval_count: int = 0,
    total_duration_ms: float = 0,
    **extra,
) -> str:
    """Log a chat response with optional Ollama metrics."""
    data = {
        "model": model,
        "response_preview": _truncate(response_preview, 100),
        "metrics": {
            "eval_count": eval_count,
            "prompt_eval_count": prompt_eval_count,
            "total_duration_ms": round(total_duration_ms, 2),
        },
    }
    data.update(extra)
    return _emit("chat", data)


def log_skill_call(skill_name: str, params: Optional[dict] = None, **extra) -> str:
    """Log a skill invocation."""
    data = {
        "skill_name": skill_name,
        "params": _sanitize(params or {}),
    }
    data.update(extra)
    return _emit("skill", data)


def log_policy_decision(
    action: str,
    zone: str = "",
    decision: str = "",
    risk_level: str = "",
    reason: str = "",
    **extra,
) -> str:
    """Log a policy engine decision."""
    data = {
        "action": action,
        "zone": zone,
        "decision": decision,
        "risk_level": risk_level,
        "reason": _truncate(reason),
    }
    data.update(extra)
    return _emit("policy", data)


def log_approval_event(
    approval_id: str,
    action: str,
    zone: str = "",
    risk_level: str = "",
    status: str = "",
    description: str = "",
    response_time_ms: float = 0,
    **extra,
) -> str:
    """Log an approval gate event (requested, approved, denied, timeout)."""
    data = {
        "approval_id": approval_id,
        "action": action,
        "zone": zone,
        "risk_level": risk_level,
        "status": status,
        "description": _truncate(description),
    }
    if response_time_ms:
        data["response_time_ms"] = round(response_time_ms, 2)
    data.update(extra)
    return _emit("approval", data)


# ---------------------------------------------------------------------------
# Query helper (for dashboard)
# ---------------------------------------------------------------------------
def get_recent_logs(
    redis_client,
    log_type: str = "all",
    count: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Retrieve recent log entries from Redis.

    Args:
        redis_client: Redis connection to query.
        log_type: "all", "chat", "skill", "policy", or "approval".
        count: Number of entries to return.
        offset: Number of entries to skip from the head.

    Returns:
        List of parsed log entry dicts, newest first.
    """
    if redis_client is None:
        return []
    key = f"logs:{log_type}"
    try:
        raw_entries = redis_client.lrange(key, offset, offset + count - 1)
        return [json.loads(entry) for entry in raw_entries]
    except Exception:
        return []
