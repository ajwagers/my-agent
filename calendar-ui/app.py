"""
Streamlit Calendar UI — reads/writes Microsoft Calendar via MS Graph API.
Shares the MSAL token cache with agent-core via the agent-identity volume.
"""

import os
from datetime import date, datetime, timedelta

import requests
import streamlit as st
from streamlit_calendar import calendar as st_calendar

from calendar_auth import get_ms_token

st.set_page_config(page_title="Calendar", page_icon="📅", layout="wide")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

def _try_get_token():
    try:
        return get_ms_token()
    except RuntimeError:
        return None


token = _try_get_token()
if token is None:
    st.warning(
        "Not authenticated with Microsoft Calendar. "
        "Run the following to authenticate:\n\n"
        "```\ndocker exec -it agent-core agent calendar-auth\n```"
    )
    st.stop()


# ---------------------------------------------------------------------------
# MS Graph helpers
# ---------------------------------------------------------------------------

def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def fetch_events(tok, start: date, end: date):
    url = (
        f"{_GRAPH_BASE}/me/calendarView"
        f"?startDateTime={start.isoformat()}T00:00:00Z"
        f"&endDateTime={end.isoformat()}T23:59:59Z"
        f"&$select=id,subject,start,end"
        f"&$orderby=start/dateTime&$top=100"
    )
    resp = requests.get(url, headers=_headers(tok), timeout=15)
    resp.raise_for_status()
    return resp.json().get("value", [])


def create_event(tok, title: str, start_dt: str, end_dt: str):
    body = {
        "subject": title,
        "start": {"dateTime": start_dt, "timeZone": "UTC"},
        "end": {"dateTime": end_dt, "timeZone": "UTC"},
    }
    resp = requests.post(f"{_GRAPH_BASE}/me/events", headers=_headers(tok), json=body, timeout=15)
    resp.raise_for_status()


def delete_event(tok, event_id: str):
    resp = requests.delete(
        f"{_GRAPH_BASE}/me/events/{event_id}",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

def _month_range():
    today = date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    return start, end


def _week_range():
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def _to_fc_events(raw):
    out = []
    for ev in raw:
        out.append({
            "id": ev.get("id", ""),
            "title": ev.get("subject", "(no title)"),
            "start": ev.get("start", {}).get("dateTime", ""),
            "end": ev.get("end", {}).get("dateTime", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📅 Calendar")

    view = st.radio("View", ["Month", "Week"], horizontal=True)

    st.divider()
    st.subheader("Add Event")

    with st.form("add_event", clear_on_submit=True):
        title = st.text_input("Title", placeholder="Event title")
        event_date = st.date_input("Date", value=date.today())
        start_time = st.time_input(
            "Start time",
            value=datetime.now().replace(minute=0, second=0, microsecond=0).time(),
        )
        duration_mins = st.number_input("Duration (min)", min_value=15, max_value=480, value=60, step=15)
        add_btn = st.form_submit_button("Add Event", use_container_width=True)

    if add_btn:
        if not title.strip():
            st.sidebar.error("Title is required.")
        else:
            try:
                start_dt = datetime.combine(event_date, start_time)
                end_dt = start_dt + timedelta(minutes=int(duration_mins))
                create_event(
                    token,
                    title.strip(),
                    start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                )
                st.sidebar.success(f"Added: {title}")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Failed: {e}")

    st.divider()
    st.subheader("Next 7 Days")


# ---------------------------------------------------------------------------
# Main — fetch events + calendar component
# ---------------------------------------------------------------------------

if view == "Month":
    range_start, range_end = _month_range()
    fc_view = "dayGridMonth"
else:
    range_start, range_end = _week_range()
    fc_view = "timeGridWeek"

try:
    raw_events = fetch_events(token, range_start, range_end)
except Exception as e:
    st.error(f"Failed to load events: {e}")
    raw_events = []

fc_events = _to_fc_events(raw_events)

calendar_options = {
    "initialView": fc_view,
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "",
    },
    "height": 680,
    "selectable": True,
    "editable": False,
}

result = st_calendar(events=fc_events, options=calendar_options, key=f"cal_{view}")

# Event click → delete
if result and result.get("eventClick"):
    clicked_id = result["eventClick"]["event"]["id"]
    clicked_title = result["eventClick"]["event"]["title"]
    st.info(f"Selected: **{clicked_title}**")
    if st.button(f"🗑 Delete '{clicked_title}'", type="primary"):
        try:
            delete_event(token, clicked_id)
            st.success("Deleted.")
            st.rerun()
        except Exception as e:
            st.error(f"Delete failed: {e}")

# Populate sidebar upcoming list from already-fetched events
with st.sidebar:
    today = date.today()
    cutoff = (today + timedelta(days=7)).isoformat()
    upcoming = [
        ev for ev in raw_events
        if ev.get("start", {}).get("dateTime", "") >= today.isoformat()
        and ev.get("start", {}).get("dateTime", "") <= cutoff + "T23:59:59"
    ]
    if not upcoming:
        st.caption("No events in the next 7 days.")
    else:
        for ev in upcoming[:20]:
            dt = ev.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
            st.markdown(f"- **{ev.get('subject', '?')}** — {dt}")
