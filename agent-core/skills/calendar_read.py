"""
Calendar read skill — list events from Outlook (MS Graph) or Proton (CalDAV).
"""

import os
from typing import Any, Dict, List, Tuple

import requests

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_PROTON_CALDAV_URL = os.getenv("PROTON_CALDAV_URL", "http://proton-bridge:1080")
_PROTON_CALDAV_USER = os.getenv("PROTON_CALDAV_USER", "")
_PROTON_CALDAV_PASSWORD = os.getenv("PROTON_CALDAV_PASSWORD", "")


def _format_event(ev: dict) -> str:
    """Format a single MS Graph event dict into a readable string."""
    title = ev.get("subject", "(no title)")
    start = ev.get("start", {}).get("dateTime", "?")[:16].replace("T", " ")
    end = ev.get("end", {}).get("dateTime", "?")[:16].replace("T", " ")
    location = ev.get("location", {}).get("displayName", "")
    line = f"• {title} | {start} → {end}"
    if location:
        line += f" @ {location}"
    return line


def _fetch_outlook_events(start: str, end: str) -> List[str]:
    from calendar_auth import get_ms_token

    token = get_ms_token()
    url = (
        f"{_GRAPH_BASE}/me/calendarView"
        f"?startDateTime={start}T00:00:00Z&endDateTime={end}T23:59:59Z"
        f"&$select=subject,start,end,location&$orderby=start/dateTime&$top=50"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    resp.raise_for_status()
    events = resp.json().get("value", [])
    return [_format_event(e) for e in events]


def _fetch_proton_events(start: str, end: str) -> List[str]:
    import caldav
    from datetime import datetime, timezone

    client = caldav.DAVClient(
        url=f"{_PROTON_CALDAV_URL}/dav/",
        username=_PROTON_CALDAV_USER,
        password=_PROTON_CALDAV_PASSWORD,
    )
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        return []

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    lines = []
    for cal in calendars:
        results = cal.date_search(start=start_dt, end=end_dt, expand=True)
        for event in results:
            vevent = event.vobject_instance.vevent
            summary = getattr(vevent, "summary", None)
            title = summary.value if summary else "(no title)"
            dtstart = vevent.dtstart.value
            dtend = vevent.dtend.value if hasattr(vevent, "dtend") else dtstart
            start_str = str(dtstart)[:16]
            end_str = str(dtend)[:16]
            location = getattr(vevent, "location", None)
            line = f"• {title} | {start_str} → {end_str}"
            if location:
                line += f" @ {location.value}"
            lines.append(line)
    return lines


class CalendarReadSkill(SkillBase):
    """List calendar events from Outlook or Proton Calendar."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="calendar_read",
            description=(
                "List calendar events for a date range. "
                "Specify calendar: 'outlook' or 'proton', and start/end dates (YYYY-MM-DD)."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="calendar_read",
            requires_approval=False,
            max_calls_per_turn=5,
            private_channels=frozenset({"telegram", "cli"}),
            parameters={
                "type": "object",
                "properties": {
                    "calendar": {
                        "type": "string",
                        "enum": ["outlook", "proton"],
                        "description": "Which calendar backend to use.",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format.",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format.",
                    },
                },
                "required": ["calendar", "start", "end"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        cal = params.get("calendar", "")
        if cal not in ("outlook", "proton"):
            return False, "Parameter 'calendar' must be 'outlook' or 'proton'"
        import re
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for field in ("start", "end"):
            val = params.get(field, "")
            if not isinstance(val, str) or not date_re.match(val):
                return False, f"Parameter '{field}' must be a date in YYYY-MM-DD format"
        if params["start"] > params["end"]:
            return False, "Parameter 'start' must not be after 'end'"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        calendar = params["calendar"]
        start = params["start"]
        end = params["end"]
        try:
            if calendar == "outlook":
                events = _fetch_outlook_events(start, end)
            else:
                events = _fetch_proton_events(start, end)
            return {"events": events, "calendar": calendar, "start": start, "end": end}
        except Exception as exc:
            return {"error": str(exc)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[calendar_read] {result['error']}"
        if isinstance(result, dict):
            events = result.get("events", [])
            cal = result.get("calendar", "")
            start = result.get("start", "")
            end = result.get("end", "")
            if not events:
                return f"No events found on {cal} calendar from {start} to {end}."
            header = f"Events on {cal} calendar ({start} to {end}):\n"
            return header + "\n".join(events[:50])
        return str(result)
