"""
Calendar write skill — create, update, or delete events in Outlook (MS Graph)
or Proton Calendar (CalDAV).
"""

import os
from typing import Any, Dict, Optional, Tuple

import requests

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_PROTON_CALDAV_URL = os.getenv("PROTON_CALDAV_URL", "http://proton-bridge:1080")
_PROTON_CALDAV_USER = os.getenv("PROTON_CALDAV_USER", "")
_PROTON_CALDAV_PASSWORD = os.getenv("PROTON_CALDAV_PASSWORD", "")


def _outlook_create(title: str, start: str, end: str, description: Optional[str]) -> dict:
    from calendar_auth import get_ms_token

    token = get_ms_token()
    body: Dict[str, Any] = {
        "subject": title,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    if description:
        body["body"] = {"contentType": "text", "content": description}
    resp = requests.post(
        f"{_GRAPH_BASE}/me/events",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    ev = resp.json()
    return {"event_id": ev.get("id", ""), "title": title, "start": start, "end": end}


def _outlook_update(event_id: str, title: Optional[str], start: Optional[str],
                    end: Optional[str], description: Optional[str]) -> dict:
    from calendar_auth import get_ms_token

    token = get_ms_token()
    body: Dict[str, Any] = {}
    if title:
        body["subject"] = title
    if start:
        body["start"] = {"dateTime": start, "timeZone": "UTC"}
    if end:
        body["end"] = {"dateTime": end, "timeZone": "UTC"}
    if description:
        body["body"] = {"contentType": "text", "content": description}
    resp = requests.patch(
        f"{_GRAPH_BASE}/me/events/{event_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return {"event_id": event_id, "updated": True}


def _outlook_delete(event_id: str) -> dict:
    from calendar_auth import get_ms_token

    token = get_ms_token()
    resp = requests.delete(
        f"{_GRAPH_BASE}/me/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return {"event_id": event_id, "deleted": True}


def _proton_create(title: str, start: str, end: str, description: Optional[str]) -> dict:
    import caldav
    from icalendar import Calendar, Event
    from datetime import datetime
    import uuid as uuid_module

    client = caldav.DAVClient(
        url=f"{_PROTON_CALDAV_URL}/dav/",
        username=_PROTON_CALDAV_USER,
        password=_PROTON_CALDAV_PASSWORD,
    )
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("No Proton calendars found")

    uid = str(uuid_module.uuid4())
    cal = Calendar()
    ev = Event()
    ev.add("summary", title)
    ev.add("dtstart", datetime.fromisoformat(start))
    ev.add("dtend", datetime.fromisoformat(end))
    ev.add("uid", uid)
    if description:
        ev.add("description", description)
    cal.add_component(ev)

    calendars[0].save_event(cal.to_ical().decode())
    return {"event_id": uid, "title": title, "start": start, "end": end}


def _proton_update(event_id: str, title: Optional[str], start: Optional[str],
                   end: Optional[str], description: Optional[str]) -> dict:
    import caldav

    client = caldav.DAVClient(
        url=f"{_PROTON_CALDAV_URL}/dav/",
        username=_PROTON_CALDAV_USER,
        password=_PROTON_CALDAV_PASSWORD,
    )
    principal = client.principal()
    for cal in principal.calendars():
        for ev in cal.events():
            uid = str(ev.vobject_instance.vevent.uid.value)
            if uid == event_id:
                vevent = ev.vobject_instance.vevent
                if title:
                    vevent.summary.value = title
                if description:
                    if hasattr(vevent, "description"):
                        vevent.description.value = description
                ev.save()
                return {"event_id": event_id, "updated": True}
    raise RuntimeError(f"Event not found: {event_id}")


def _proton_delete(event_id: str) -> dict:
    import caldav

    client = caldav.DAVClient(
        url=f"{_PROTON_CALDAV_URL}/dav/",
        username=_PROTON_CALDAV_USER,
        password=_PROTON_CALDAV_PASSWORD,
    )
    principal = client.principal()
    for cal in principal.calendars():
        for ev in cal.events():
            uid = str(ev.vobject_instance.vevent.uid.value)
            if uid == event_id:
                ev.delete()
                return {"event_id": event_id, "deleted": True}
    raise RuntimeError(f"Event not found: {event_id}")


class CalendarWriteSkill(SkillBase):
    """Create, update, or delete calendar events in Outlook or Proton Calendar."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="calendar_write",
            description=(
                "Create, update, or delete a calendar event. "
                "Specify action: 'create', 'update', or 'delete'. "
                "Specify calendar: 'outlook' or 'proton'. "
                "Owner approval required."
            ),
            risk_level=RiskLevel.HIGH,
            rate_limit="calendar_write",
            requires_approval=True,
            max_calls_per_turn=3,
            private_channels=frozenset({"telegram", "cli"}),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "delete"],
                        "description": "The operation to perform.",
                    },
                    "calendar": {
                        "type": "string",
                        "enum": ["outlook", "proton"],
                        "description": "Which calendar backend to use.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title (required for create, optional for update).",
                    },
                    "event_start": {
                        "type": "string",
                        "description": "Event start in ISO 8601 format (required for create).",
                    },
                    "event_end": {
                        "type": "string",
                        "description": "Event end in ISO 8601 format (required for create).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description/notes.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Event ID (required for update and delete).",
                    },
                },
                "required": ["action", "calendar"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if action not in ("create", "update", "delete"):
            return False, "Parameter 'action' must be 'create', 'update', or 'delete'"

        cal = params.get("calendar", "")
        if cal not in ("outlook", "proton"):
            return False, "Parameter 'calendar' must be 'outlook' or 'proton'"

        if action == "create":
            if not params.get("title", "").strip():
                return False, "Parameter 'title' is required for create"
            if not params.get("event_start", "").strip():
                return False, "Parameter 'event_start' is required for create"
            if not params.get("event_end", "").strip():
                return False, "Parameter 'event_end' is required for create"

        if action in ("update", "delete"):
            if not params.get("event_id", "").strip():
                return False, f"Parameter 'event_id' is required for {action}"

        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        action = params["action"]
        calendar = params["calendar"]
        title = params.get("title", "").strip()
        start = params.get("event_start", "").strip()
        end = params.get("event_end", "").strip()
        description = params.get("description", "").strip() or None
        event_id = params.get("event_id", "").strip()

        try:
            if calendar == "outlook":
                if action == "create":
                    return _outlook_create(title, start, end, description)
                elif action == "update":
                    return _outlook_update(event_id, title or None, start or None,
                                           end or None, description)
                else:
                    return _outlook_delete(event_id)
            else:
                if action == "create":
                    return _proton_create(title, start, end, description)
                elif action == "update":
                    return _proton_update(event_id, title or None, start or None,
                                          end or None, description)
                else:
                    return _proton_delete(event_id)
        except Exception as exc:
            return {"error": str(exc)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[calendar_write] {result['error']}"
        if isinstance(result, dict):
            if result.get("deleted"):
                return f"Event {result.get('event_id', '')} deleted successfully."
            if result.get("updated"):
                return f"Event {result.get('event_id', '')} updated successfully."
            title = result.get("title", "")
            start = result.get("start", "")
            end = result.get("end", "")
            event_id = result.get("event_id", "")
            return f"Event created: '{title}' from {start} to {end} (ID: {event_id})"
        return str(result)
