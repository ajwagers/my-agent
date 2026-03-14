"""
CreateTaskSkill — LLM-callable skill to create a scheduled job.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class CreateTaskSkill(SkillBase):
    """Schedule a task to run once, at a specific time, or on a recurring schedule."""

    def __init__(self, redis_client):
        self._redis = redis_client

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="create_task",
            description=(
                "Schedule a task or reminder to run in the future. "
                "Use this when the user asks to be reminded of something, "
                "wants a recurring job (e.g. 'every day summarize the news'), "
                "or wants to run something later. "
                "Supports: one_shot (run ASAP or after a delay), "
                "scheduled (specific datetime), recurring (repeat every N seconds)."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="create_task",
            requires_approval=False,
            max_calls_per_turn=3,
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task to execute — a natural language prompt run through the full tool loop.",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["one_shot", "scheduled", "recurring"],
                        "description": (
                            "one_shot: run once (immediately or after delay_seconds). "
                            "scheduled: run at a specific time (requires run_at). "
                            "recurring: repeat every interval_seconds."
                        ),
                    },
                    "run_at": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime string (e.g. '2025-12-01T09:00:00Z'). "
                            "Required for job_type=scheduled."
                        ),
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Repeat interval in seconds. Required for job_type=recurring.",
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Delay in seconds before running. Optional for job_type=one_shot (default 0).",
                    },
                },
                "required": ["prompt", "job_type"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        prompt = params.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            return False, "Parameter 'prompt' must be a non-empty string"
        if len(prompt) > 500:
            return False, "Parameter 'prompt' must be under 500 characters"

        job_type = params.get("job_type", "")
        if job_type not in ("one_shot", "scheduled", "recurring"):
            return False, "Parameter 'job_type' must be one of: one_shot, scheduled, recurring"

        if job_type == "scheduled":
            run_at = params.get("run_at")
            if not run_at:
                return False, "Parameter 'run_at' is required for scheduled jobs"
            try:
                dt = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= datetime.now(timezone.utc):
                    return False, "Parameter 'run_at' must be in the future"
            except (ValueError, AttributeError):
                return False, "Parameter 'run_at' must be a valid ISO 8601 datetime"

        if job_type == "recurring":
            interval = params.get("interval_seconds")
            if interval is None:
                return False, "Parameter 'interval_seconds' is required for recurring jobs"
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                return False, "Parameter 'interval_seconds' must be an integer"
            if interval <= 0:
                return False, "Parameter 'interval_seconds' must be greater than 0"

        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        from job_manager import JobManager

        user_id = params.pop("_user_id", "default")
        job_manager = JobManager(self._redis)

        job_type = params["job_type"]
        prompt = params["prompt"]
        run_at_ts: float | None = None
        interval_seconds: int | None = None
        delay_seconds = int(params.get("delay_seconds", 0) or 0)

        if job_type == "scheduled":
            run_at_str = str(params["run_at"])
            dt = datetime.fromisoformat(run_at_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            run_at_ts = dt.timestamp()

        if job_type == "recurring":
            interval_seconds = int(params["interval_seconds"])

        job_id = job_manager.create(
            user_id=user_id,
            prompt=prompt,
            job_type=job_type,
            run_at=run_at_ts,
            delay_seconds=delay_seconds,
            interval_seconds=interval_seconds,
        )

        job = job_manager.get(job_id)
        return {"job_id": job_id, "job_type": job_type, "run_at": job["run_at"]}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        job_id = result.get("job_id", "?")
        job_type = result.get("job_type", "?")
        run_at = result.get("run_at", 0)
        try:
            dt = datetime.fromtimestamp(float(run_at), tz=timezone.utc)
            human_time = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            human_time = str(run_at)
        return f"Job scheduled (ID: {job_id}). Type: {job_type}, runs at: {human_time}"
