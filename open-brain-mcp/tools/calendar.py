"""Unified calendar — local store + Outlook sync → Skylight."""
import json
import os
from datetime import datetime, timezone, timedelta

import httpx

from db import get_pool

MS_GRAPH_CLIENT_ID = os.getenv("MS_GRAPH_CLIENT_ID", "")
_TOKEN_CACHE = "/agent/ms_token_cache.bin"


# ── Family members ────────────────────────────────────────────────────────────

async def add_family_member(name: str, role: str = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO family_members (name, role) VALUES ($1, $2) RETURNING id, name, role",
            name, role,
        )
    return {"id": str(row["id"]), "name": row["name"], "role": row["role"]}


async def list_family_members() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, role FROM family_members ORDER BY name")
    return [{"id": str(r["id"]), "name": r["name"], "role": r["role"]} for r in rows]


# ── Calendar events ───────────────────────────────────────────────────────────

async def add_calendar_event(
    title: str,
    start_time: str,
    event_type: str = "family",
    description: str = None,
    end_time: str = None,
    all_day: bool = False,
    family_member_id: str = None,
    recurrence_rule: str = None,
    location: str = None,
    metadata: dict = None,
    sync_outlook: bool = True,
) -> dict:
    pool = await get_pool()
    start_dt = _parse_dt(start_time)
    end_dt = _parse_dt(end_time) if end_time else None
    member_id = _uuid_or_none(family_member_id)
    meta_json = json.dumps(metadata or {})

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO calendar_events
               (title, description, event_type, start_time, end_time, all_day,
                family_member_id, recurrence_rule, location, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
               RETURNING id, title, event_type, start_time""",
            title, description, event_type, start_dt, end_dt, all_day,
            member_id, recurrence_rule, location, meta_json,
        )
        event_id = str(row["id"])

    outlook_id = None
    if sync_outlook and event_type in ("appointment", "family", "production", "market_event", "business"):
        outlook_id = await _sync_to_outlook(event_id, title, description, start_dt, end_dt, all_day, location)
        if outlook_id:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE calendar_events SET outlook_event_id=$1 WHERE id=$2",
                    outlook_id, event_id,
                )

    return {
        "id": event_id,
        "title": row["title"],
        "event_type": row["event_type"],
        "start_time": row["start_time"].isoformat(),
        "outlook_synced": bool(outlook_id),
    }


async def update_calendar_event(event_id: str, **kwargs) -> dict:
    pool = await get_pool()
    allowed = {"title", "description", "start_time", "end_time", "location", "event_type", "metadata"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return {"error": "No valid fields to update"}

    set_clauses = []
    args = []
    for k, v in updates.items():
        args.append(_parse_dt(v) if k in ("start_time", "end_time") else v)
        set_clauses.append(f"{k} = ${len(args)}")

    args.append(event_id)
    sql = f"UPDATE calendar_events SET {', '.join(set_clauses)}, updated_at=NOW() WHERE id=${len(args)} RETURNING id, title"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": "Event not found"}
    return {"id": str(row["id"]), "title": row["title"], "updated": True}


async def delete_calendar_event(event_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Grab outlook_event_id before deleting
        row = await conn.fetchrow(
            "SELECT outlook_event_id FROM calendar_events WHERE id=$1", event_id
        )
        if not row:
            return {"error": "Event not found"}
        await conn.execute("DELETE FROM calendar_events WHERE id=$1", event_id)
    return {"deleted": True, "id": event_id}


async def get_week_schedule(date: str = None, family_member_id: str = None) -> list[dict]:
    pool = await get_pool()
    if date:
        base = _parse_dt(date).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    else:
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Start of week (Monday)
    start = base - timedelta(days=base.weekday())
    end = start + timedelta(days=7)

    conditions = ["start_time >= $1 AND start_time < $2"]
    args = [start, end]
    if family_member_id:
        args.append(family_member_id)
        conditions.append(f"family_member_id = ${len(args)}")

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT ce.*, fm.name AS member_name
                FROM calendar_events ce
                LEFT JOIN family_members fm ON ce.family_member_id = fm.id
                WHERE {where} ORDER BY start_time""",
            *args,
        )
    return [_event_row_to_dict(r) for r in rows]


async def search_events(query: str, event_type: str = None, days_ahead: int = 30) -> list[dict]:
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    conditions = ["start_time >= $1 AND start_time <= $2",
                  "(LOWER(title) LIKE $3 OR LOWER(description) LIKE $3)"]
    args = [now, end, f"%{query.lower()}%"]
    if event_type:
        args.append(event_type)
        conditions.append(f"event_type = ${len(args)}")
    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT ce.*, fm.name AS member_name
                FROM calendar_events ce
                LEFT JOIN family_members fm ON ce.family_member_id = fm.id
                WHERE {where} ORDER BY start_time LIMIT 20""",
            *args,
        )
    return [_event_row_to_dict(r) for r in rows]


async def get_upcoming_dates(days_ahead: int = 30) -> list[dict]:
    """Get upcoming important dates (birthdays, anniversaries, etc.)."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ce.*, fm.name AS member_name
               FROM calendar_events ce
               LEFT JOIN family_members fm ON ce.family_member_id = fm.id
               WHERE event_type = 'important_date' AND start_time >= $1 AND start_time <= $2
               ORDER BY start_time""",
            now, end,
        )
    return [_event_row_to_dict(r) for r in rows]


# ── Outlook sync helper ───────────────────────────────────────────────────────

async def _sync_to_outlook(event_id, title, description, start_dt, end_dt, all_day, location) -> str | None:
    """Write event to Outlook via MS Graph. Returns outlook_event_id or None."""
    try:
        import msal, json as _json
        cache = msal.SerializableTokenCache()
        if os.path.exists(_TOKEN_CACHE):
            with open(_TOKEN_CACHE) as f:
                cache.deserialize(f.read())

        app = msal.PublicClientApplication(MS_GRAPH_CLIENT_ID, token_cache=cache)
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(["Calendars.ReadWrite"], account=accounts[0])
        if not result or "access_token" not in result:
            return None

        end = end_dt or (start_dt + timedelta(hours=1))
        body = {
            "subject": title,
            "body": {"contentType": "text", "content": description or ""},
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }
        if location:
            body["location"] = {"displayName": location}
        if all_day:
            body["isAllDay"] = True
            body["start"] = {"date": start_dt.strftime("%Y-%m-%d"), "timeZone": "UTC"}
            body["end"] = {"date": (start_dt + timedelta(days=1)).strftime("%Y-%m-%d"), "timeZone": "UTC"}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/me/events",
                headers={"Authorization": f"Bearer {result['access_token']}",
                         "Content-Type": "application/json"},
                content=_json.dumps(body),
            )
            if resp.status_code == 201:
                return resp.json().get("id")
    except Exception:
        pass
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    from dateutil import parser as dp
    dt = dp.parse(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _uuid_or_none(s):
    if not s:
        return None
    import uuid
    try:
        return uuid.UUID(s)
    except Exception:
        return None


def _event_row_to_dict(r) -> dict:
    d = {
        "id": str(r["id"]),
        "title": r["title"],
        "event_type": r["event_type"],
        "start_time": r["start_time"].isoformat(),
        "end_time": r["end_time"].isoformat() if r["end_time"] else None,
        "all_day": r["all_day"],
        "location": r.get("location"),
        "outlook_event_id": r.get("outlook_event_id"),
    }
    if "member_name" in r.keys():
        d["family_member"] = r["member_name"]
    return d
