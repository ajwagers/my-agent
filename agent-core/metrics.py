"""
Prometheus metrics for the agent stack.

Counters and histograms are incremented in tracing.py at emit time.
Gauges (queue depth, pending approvals) are updated by a background
task in app.py every 15 seconds.

Exposed at GET /metrics (no auth — internal network only).
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
chat_requests_total = Counter(
    "agent_chat_requests_total",
    "Total incoming chat requests",
    ["channel", "model"],
)

chat_responses_total = Counter(
    "agent_chat_responses_total",
    "Total chat responses emitted",
    ["channel", "model"],
)

skill_calls_total = Counter(
    "agent_skill_calls_total",
    "Total skill invocations",
    ["skill_name"],
)

skill_errors_total = Counter(
    "agent_skill_errors_total",
    "Total skill invocations that returned an error",
    ["skill_name"],
)

policy_decisions_total = Counter(
    "agent_policy_decisions_total",
    "Policy engine decisions",
    ["decision", "zone"],
)

approval_events_total = Counter(
    "agent_approval_events_total",
    "Approval gate events",
    ["status"],
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------
chat_response_ms = Histogram(
    "agent_chat_response_ms",
    "End-to-end chat response time in milliseconds",
    ["model"],
    buckets=[250, 500, 1000, 2000, 5000, 10000, 20000, 60000],
)

# ---------------------------------------------------------------------------
# Gauges  (updated by background task in app.py)
# ---------------------------------------------------------------------------
queue_depth = Gauge(
    "agent_queue_depth",
    "Current depth of the Redis chat queue (queue:chat)",
)

pending_approvals = Gauge(
    "agent_pending_approvals",
    "Number of approval requests currently in pending state",
)
