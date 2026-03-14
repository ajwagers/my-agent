"""
CancelTaskSkill — LLM-callable skill to cancel a job by ID.
"""

from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class CancelTaskSkill(SkillBase):
    """Cancel a scheduled or recurring job by its ID."""

    def __init__(self, redis_client):
        self._redis = redis_client

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="cancel_task",
            description=(
                "Cancel a scheduled or recurring job by its ID. "
                "Use this when the user asks to cancel or remove a job or reminder."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="cancel_task",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The ID of the job to cancel.",
                    },
                },
                "required": ["job_id"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        job_id = params.get("job_id", "")
        if not isinstance(job_id, str) or not job_id.strip():
            return False, "Parameter 'job_id' must be a non-empty string"
        if len(job_id) > 64:
            return False, "Parameter 'job_id' must be 64 characters or fewer"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        from job_manager import JobManager

        user_id = params.pop("_user_id", "default")
        job_id = params["job_id"]
        job_manager = JobManager(self._redis)

        job = job_manager.get(job_id)
        if not job:
            return {"error": f"Job {job_id} not found."}
        if job.get("user_id") != user_id:
            return {"error": f"Job {job_id} does not belong to you."}

        ok = job_manager.cancel(job_id)
        if not ok:
            return {"error": f"Job {job_id} could not be cancelled (may be running)."}

        return {"job_id": job_id, "cancelled": True}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        if "error" in result:
            return result["error"]
        job_id = result.get("job_id", "?")
        return f"Job {job_id} cancelled."
