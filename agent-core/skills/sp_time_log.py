"""Skill: sp_time_log — Summit Pine labour hour tracking."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineTimeLogSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_time_log",
            description=(
                "Track Summit Pine labour hours and costs. "
                "Actions: log_hours (record a work session — parse start/end times or stated hours), "
                "list_hours (view time log with optional date/person filter), "
                "time_summary (totals by person for a month or all-time). "
                "Use log_hours whenever the user says 'I worked X hours', 'started at Xam ended at Xpm', "
                "or any description of time spent on production, packaging, admin, etc."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_time_log",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["log_hours", "list_hours", "time_summary"],
                        "description": "Time log action to perform.",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Hours worked. Can be omitted when both start_time and end_time are given.",
                    },
                    "log_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today."},
                    "person": {
                        "type": "string",
                        "description": "owner | helper | contractor (default: owner).",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time — '9am', '09:00', '9:30am'. Used with end_time to compute hours.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time — '2pm', '14:00'. Used with start_time to compute hours.",
                    },
                    "task_description": {"type": "string", "description": "What was done (e.g. 'production run', 'packaging')."},
                    "hourly_rate": {"type": "number", "description": "Hourly rate in USD (leave blank for uncosted entries)."},
                    "notes": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD filter start for list_hours."},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD filter end for list_hours."},
                    "year": {"type": "integer", "description": "Year for time_summary."},
                    "month": {"type": "integer", "description": "Month (1-12) for time_summary."},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if not action:
            return False, "action is required"
        if action == "log_hours":
            has_hours = params.get("hours") is not None
            has_times = params.get("start_time") and params.get("end_time")
            if not has_hours and not has_times:
                return False, "log_hours requires either hours or both start_time and end_time"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "log_hours":
                body = {k: params[k] for k in (
                    "hours", "log_date", "person", "start_time", "end_time",
                    "task_description", "hourly_rate", "notes"
                ) if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/log_hours", json=body)
            elif action == "list_hours":
                body = {k: params[k] for k in ("start_date", "end_date", "person", "limit") if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/list_time_logs", json=body)
            elif action == "time_summary":
                query = {}
                if params.get("year"):
                    query["year"] = params["year"]
                if params.get("month"):
                    query["month"] = params["month"]
                resp = await client.get(f"{BRAIN_URL}/tools/time_summary", params=query)
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No time log entries found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
