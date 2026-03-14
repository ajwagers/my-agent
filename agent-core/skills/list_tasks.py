"""
ListTasksSkill — LLM-callable skill to list the current user's jobs.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

_VALID_STATUSES = {"all", "pending", "running", "completed", "failed", "cancelled"}


class ListTasksSkill(SkillBase):
    """List scheduled and recurring jobs for the current user."""

    def __init__(self, redis_client):
        self._redis = redis_client

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="list_tasks",
            description=(
                "List the current user's scheduled or recurring jobs. "
                "Use this when the user asks 'what jobs do I have?', "
                "'show my reminders', or similar."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="list_tasks",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["all", "pending", "running", "completed", "failed", "cancelled"],
                        "description": "Filter by job status. Default is 'all'.",
                    },
                },
                "required": [],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        status = params.get("status", "all")
        if status not in _VALID_STATUSES:
            return False, f"Parameter 'status' must be one of: {', '.join(sorted(_VALID_STATUSES))}"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        from job_manager import JobManager

        user_id = params.pop("_user_id", "default")
        job_manager = JobManager(self._redis)

        jobs = job_manager.list_for_user(user_id)
        status_filter = params.get("status", "all")
        if status_filter != "all":
            jobs = [j for j in jobs if j.get("status") == status_filter]

        return {"jobs": jobs}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        jobs = result.get("jobs", [])
        if not jobs:
            return "No jobs found."
        lines = []
        for n, job in enumerate(jobs, 1):
            status = job.get("status", "?")
            prompt = job.get("prompt", "")[:60]
            run_at = job.get("run_at", 0)
            job_id = job.get("id", "?")
            try:
                dt = datetime.fromtimestamp(float(run_at), tz=timezone.utc)
                time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                time_str = str(run_at)
            lines.append(f"{n}. [{status}] {prompt} — runs at {time_str} (ID: {job_id})")
        return "\n".join(lines)
