"""
Agent Health Dashboard — real-time operational visibility for the agent stack.

Streamlit app that reads structured logs from Redis and probes service
health endpoints. Auto-refreshes every REFRESH_INTERVAL seconds.
"""

import os
import time
from datetime import datetime

import redis
import streamlit as st

from health_probes import check_all, check_ollama
from redis_queries import (
    count_logs_by_type,
    get_activity_stats,
    get_approval_history,
    get_pending_approvals,
    get_recent_logs,
    get_security_events,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Agent Health Dashboard",
    page_icon="📊",
    layout="wide",
)

REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "10"))

# ---------------------------------------------------------------------------
# Redis connection (cached across reruns)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_redis():
    url = os.getenv("REDIS_URL", "redis://redis:6379")
    return redis.from_url(url, decode_responses=True)


try:
    r = get_redis()
    r.ping()
    redis_ok = True
except Exception:
    r = None
    redis_ok = False

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Agent Health Dashboard")
st.caption(
    f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}  |  "
    f"Auto-refresh: {REFRESH_INTERVAL}s"
)

if not redis_ok:
    st.error("Cannot connect to Redis. Dashboard data is unavailable.")
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# =========================================================================
# Panel 1: System Health
# =========================================================================
st.header("System Health")

health = check_all(r)

STATUS_ICONS = {"healthy": "🟢", "unhealthy": "🔴", "unknown": "🟡"}

services = [
    ("agent-core", "agent_core"),
    ("ollama", "ollama"),
    ("chromadb", "chromadb"),
    ("redis", "redis"),
    ("web-ui", "web_ui"),
    ("telegram", "telegram_gateway"),
]

cols = st.columns(len(services))
for col, (label, key) in zip(cols, services):
    status, details = health.get(key, ("unknown", {}))
    icon = STATUS_ICONS.get(status, "🟡")
    with col:
        st.metric(label=f"{icon} {label}", value=status.upper())
        if details:
            with st.expander("Details"):
                for k, v in details.items():
                    if isinstance(v, list):
                        st.text(f"{k}: {', '.join(str(i) for i in v)}")
                    else:
                        st.text(f"{k}: {v}")

st.divider()

# =========================================================================
# Panel 2 + 3: Activity (left) | Queue & Jobs (right)
# =========================================================================
left_col, right_col = st.columns([3, 2])

# --- Panel 2: Activity ---
with left_col:
    st.header("Activity")

    stats = get_activity_stats(r, hours=24)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Requests (24h)", stats["total_requests"])
    m2.metric("Requests (1h)", stats["requests_this_hour"])
    m3.metric("Channels Active", len(stats["requests_by_channel"]))
    m4.metric("Skills Called", sum(stats["skill_counts"].values()))

    if stats["requests_by_channel"]:
        st.subheader("Requests by Channel")
        st.bar_chart(stats["requests_by_channel"])

    if stats["skill_counts"]:
        st.subheader("Skill Calls")
        st.bar_chart(stats["skill_counts"])

    if stats["avg_response_time_by_model"]:
        st.subheader("Avg Response Time (ms)")
        for model, avg_ms in stats["avg_response_time_by_model"].items():
            st.text(f"  {model}: {avg_ms:.0f} ms")

    # Policy decisions summary
    pd = stats["policy_decisions"]
    if any(pd.values()):
        st.subheader("Policy Decisions (24h)")
        p1, p2, p3 = st.columns(3)
        p1.metric("Allowed", pd["allow"])
        p2.metric("Denied", pd["deny"])
        p3.metric("Needs Approval", pd["requires_approval"])

# --- Panel 3: Queue & Jobs (placeholder) ---
with right_col:
    st.header("Queue & Jobs")
    st.info(
        "Job queue monitoring will be available in Phase 5. "
        "This panel will show pending, running, completed, and scheduled jobs."
    )

    pending = get_pending_approvals(r)
    if pending:
        st.subheader(f"Pending Approvals ({len(pending)})")
        for item in pending:
            action = item.get("action", "unknown")
            desc = item.get("description", "N/A")
            risk = item.get("risk_level", "?")
            RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
            emoji = RISK_EMOJI.get(risk, "⚪")
            with st.expander(f"{emoji} {action}: {desc[:60]}"):
                for k, v in item.items():
                    st.text(f"{k}: {v}")
    else:
        st.caption("No pending approvals.")

    # Log volume summary
    counts = count_logs_by_type(r)
    if counts:
        st.subheader("Log Volume")
        for log_type, count in counts.items():
            st.text(f"  logs:{log_type} — {count} entries")

st.divider()

# =========================================================================
# Panel 4: Recent Activity Feed
# =========================================================================
st.header("Recent Activity")

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    log_type_filter = st.selectbox(
        "Event Type", ["all", "chat", "skill", "policy", "approval"]
    )
with filter_col2:
    entry_count = st.slider("Entries", 10, 100, 50)
with filter_col3:
    channel_filter = st.text_input("Channel Filter", placeholder="e.g. telegram")

logs = get_recent_logs(r, log_type=log_type_filter, count=entry_count)

if channel_filter:
    logs = [
        l for l in logs if l.get("channel", "").lower() == channel_filter.lower()
    ]

if not logs:
    st.caption("No activity recorded yet.")
else:
    for entry in logs:
        ts = datetime.fromtimestamp(entry.get("timestamp", 0)).strftime("%H:%M:%S")
        event = entry.get("event_type", "?")
        channel = entry.get("channel", "-") or "-"
        user = entry.get("user_id", "-") or "-"

        # Build a one-line summary based on event type
        if event == "chat":
            model = entry.get("model", "?")
            preview = entry.get("message_preview", entry.get("response_preview", ""))
            summary = f"[{model}] {preview[:60]}"
            if "metrics" in entry:
                ms = entry["metrics"].get("total_duration_ms", 0)
                if ms:
                    summary += f" ({ms:.0f}ms)"
        elif event == "skill":
            summary = f"skill:{entry.get('skill_name', '?')}"
        elif event == "policy":
            summary = (
                f"{entry.get('action', '?')} -> "
                f"{entry.get('decision', '?')} "
                f"({entry.get('zone', '?')})"
            )
        elif event == "approval":
            st_emoji = {
                "pending": "🟡", "approved": "🟢",
                "denied": "🔴", "timeout": "⏰",
            }
            a_status = entry.get("status", "?")
            summary = (
                f"{st_emoji.get(a_status, '⚪')} {a_status}: "
                f"{entry.get('description', '')[:60]}"
            )
        else:
            summary = str(entry)[:80]

        EVENT_BADGES = {
            "chat": ":blue[chat]",
            "skill": ":green[skill]",
            "policy": ":orange[policy]",
            "approval": ":red[approval]",
        }
        badge = EVENT_BADGES.get(event, f"`{event}`")

        st.markdown(f"**{ts}** {badge} `{channel}` {user} — {summary}")

st.divider()

# =========================================================================
# Panel 5: Security & Audit
# =========================================================================
st.header("Security & Audit")

sec_left, sec_right = st.columns(2)

with sec_left:
    st.subheader("Policy Denials")
    denials = get_security_events(r, count=20)
    if denials:
        for d in denials:
            ts = datetime.fromtimestamp(d.get("timestamp", 0)).strftime("%H:%M:%S")
            event = d.get("event_type", "?")
            if event == "policy":
                reason = d.get("reason", "N/A")[:80]
                st.markdown(
                    f"**{ts}** {d.get('action', '?')} in "
                    f"{d.get('zone', '?')} — {reason}"
                )
            elif event == "approval":
                st.markdown(
                    f"**{ts}** approval {d.get('status', '?')}: "
                    f"{d.get('description', '')[:60]}"
                )
    else:
        st.success("No denied actions.")

with sec_right:
    st.subheader("Approval History")
    approvals = get_approval_history(r, count=20)
    if approvals:
        for a in approvals:
            ts = datetime.fromtimestamp(a.get("timestamp", 0)).strftime("%H:%M:%S")
            status = a.get("status", "?")
            STATUS_EMOJI = {
                "pending": "🟡", "approved": "🟢",
                "denied": "🔴", "timeout": "⏰",
            }
            emoji = STATUS_EMOJI.get(status, "⚪")
            st.markdown(
                f"**{ts}** {emoji} {status}: "
                f"{a.get('description', '')[:60]}"
            )
    else:
        st.caption("No approval events yet.")

# =========================================================================
# Auto-refresh
# =========================================================================
time.sleep(REFRESH_INTERVAL)
st.rerun()
