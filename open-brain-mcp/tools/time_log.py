"""Summit Pine labour hour tracking."""
from datetime import date, time
from typing import Optional

from db import get_pool


async def log_hours(
    hours: float = None,
    log_date: str = None,
    person: str = "owner",
    start_time: str = None,
    end_time: str = None,
    task_description: str = None,
    hourly_rate: float = None,
    notes: str = None,
) -> dict:
    """Record a work session.

    hours can be omitted when both start_time and end_time are provided —
    the duration is computed automatically.
    """
    ld = date.fromisoformat(log_date) if log_date else date.today()

    # Parse start/end times and derive hours when not explicitly given
    st_obj = _parse_time(start_time) if start_time else None
    et_obj = _parse_time(end_time) if end_time else None

    if hours is None:
        if st_obj is not None and et_obj is not None:
            import datetime as _dt
            delta = _dt.datetime.combine(_dt.date.min, et_obj) - _dt.datetime.combine(_dt.date.min, st_obj)
            hours = round(delta.total_seconds() / 3600, 2)
        else:
            return {"error": "hours is required when start_time and end_time are not both provided"}

    if hours <= 0:
        return {"error": "hours must be positive"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO sp_time_logs
               (log_date, person, hours, start_time, end_time,
                task_description, hourly_rate, notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING id, log_date, person, hours""",
            ld, person, hours, st_obj, et_obj,
            task_description, hourly_rate, notes,
        )
    return {
        "id": str(row["id"]),
        "log_date": row["log_date"].isoformat(),
        "person": row["person"],
        "hours": float(row["hours"]),
        "recorded": True,
    }


async def list_time_logs(
    start_date: str = None,
    end_date: str = None,
    person: str = None,
    limit: int = 50,
) -> list[dict]:
    pool = await get_pool()
    conditions, args = [], []
    if start_date:
        args.append(date.fromisoformat(start_date))
        conditions.append(f"log_date >= ${len(args)}")
    if end_date:
        args.append(date.fromisoformat(end_date))
        conditions.append(f"log_date <= ${len(args)}")
    if person:
        args.append(person)
        conditions.append(f"person = ${len(args)}")
    args.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM sp_time_logs {where} ORDER BY log_date DESC LIMIT ${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_row(r) for r in rows]


async def time_summary(year: int = None, month: int = None) -> dict:
    """Total hours and labour cost grouped by person for the given month."""
    pool = await get_pool()
    if year and month:
        sql = """SELECT person,
                        SUM(hours) AS total_hours,
                        SUM(hours * COALESCE(hourly_rate, 0)) AS labour_cost,
                        COUNT(*) AS sessions
                 FROM sp_time_logs
                 WHERE EXTRACT(year FROM log_date) = $1
                   AND EXTRACT(month FROM log_date) = $2
                 GROUP BY person ORDER BY total_hours DESC"""
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, year, month)
        period = f"{year}-{month:02d}"
    else:
        sql = """SELECT person,
                        SUM(hours) AS total_hours,
                        SUM(hours * COALESCE(hourly_rate, 0)) AS labour_cost,
                        COUNT(*) AS sessions
                 FROM sp_time_logs GROUP BY person ORDER BY total_hours DESC"""
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
        period = "all_time"

    by_person = [
        {
            "person": r["person"],
            "total_hours": round(float(r["total_hours"]), 2),
            "labour_cost": round(float(r["labour_cost"]), 2),
            "sessions": int(r["sessions"]),
        }
        for r in rows
    ]
    return {
        "period": period,
        "by_person": by_person,
        "total_hours": round(sum(p["total_hours"] for p in by_person), 2),
        "total_labour_cost": round(sum(p["labour_cost"] for p in by_person), 2),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_time(s: str):
    """Parse a time string like '9am', '14:30', '9:00 AM' into a time object."""
    import re
    s = s.strip().lower().replace(" ", "")
    # 12-hour: 9am, 2pm, 9:30am
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?([ap]m)", s)
    if m:
        h, mi, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if period == "pm" and h != 12:
            h += 12
        if period == "am" and h == 12:
            h = 0
        return time(h, mi)
    # 24-hour: 14:30 or 14
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", s)
    if m:
        return time(int(m.group(1)), int(m.group(2) or 0))
    return None


def _row(r) -> dict:
    return {
        "id": str(r["id"]),
        "log_date": r["log_date"].isoformat(),
        "person": r["person"],
        "hours": float(r["hours"]),
        "start_time": r["start_time"].strftime("%H:%M") if r["start_time"] else None,
        "end_time": r["end_time"].strftime("%H:%M") if r["end_time"] else None,
        "task_description": r["task_description"],
        "hourly_rate": float(r["hourly_rate"]) if r["hourly_rate"] else None,
        "notes": r["notes"],
    }
