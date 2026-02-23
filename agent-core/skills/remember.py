"""
Remember skill — store facts, observations, and preferences to long-term memory.
"""

from typing import Any, Dict, Tuple

from memory import MemoryStore
from memory_sanitizer import MemoryPoisonError, sanitize
from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

VALID_TYPES = {"fact", "observation", "preference"}


class RememberSkill(SkillBase):
    """Store a fact, observation, or preference to long-term agent memory."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="remember",
            description=(
                "Store a fact, observation, or preference to long-term memory. "
                "Use this to remember important details about the user or conversation "
                "that should persist across sessions."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="remember",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact or observation to remember (max 1000 chars).",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["fact", "observation", "preference"],
                        "description": "Category of memory: fact, observation, or preference.",
                    },
                },
                "required": ["content"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        content = params.get("content", "")
        if not isinstance(content, str):
            return False, "Parameter 'content' must be a string"
        if not content.strip():
            return False, "Parameter 'content' must not be empty"
        if len(content) > 1000:
            return False, "Parameter 'content' must be under 1000 characters"

        memory_type = params.get("type", "fact")
        if memory_type not in VALID_TYPES:
            return False, f"Parameter 'type' must be one of: {', '.join(sorted(VALID_TYPES))}"

        try:
            sanitize(content)
        except MemoryPoisonError as e:
            return False, str(e)

        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        user_id = params.pop("_user_id", "default")
        content = params["content"]
        memory_type = params.get("type", "fact")

        try:
            cleaned = sanitize(content)
        except MemoryPoisonError as e:
            return {"error": str(e)}

        try:
            store = MemoryStore()
            memory_id = store.add(
                content=cleaned,
                memory_type=memory_type,
                user_id=user_id,
                source="agent",
            )
            return {"memory_id": memory_id, "type": memory_type, "content": cleaned}
        except Exception as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[remember] {result['error']}"
        if isinstance(result, dict):
            memory_type = result.get("type", "fact")
            content = result.get("content", "")
            return f"Stored {memory_type}: {content[:100]}"
        return str(result)
