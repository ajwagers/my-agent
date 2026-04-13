"""
DeletePersonaSkill — LLM-callable skill to delete a user-created persona.
"""

from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class DeletePersonaSkill(SkillBase):
    """Delete a user-created agent persona. Built-in personas cannot be deleted."""

    def __init__(self, persona_registry):
        self._registry = persona_registry

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="delete_persona",
            description=(
                "Delete a user-created agent persona by name. "
                "Built-in personas (default, summit_pine) cannot be deleted. "
                "Use list_personas first to confirm the exact name."
            ),
            risk_level=RiskLevel.MEDIUM,
            rate_limit="delete_persona",
            requires_approval=False,
            max_calls_per_turn=2,
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The persona slug to delete (e.g. 'health_coach').",
                    },
                },
                "required": ["name"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        name = params.get("name", "")
        if not name:
            return False, "name is required"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        name = params["name"]
        try:
            deleted = self._registry.delete(name)
            if not deleted:
                return {"error": f"Persona '{name}' not found."}
            return {"deleted": name}
        except ValueError as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[delete_persona] {result['error']}"
        return f"✅ Deleted persona '{result.get('deleted', '?')}'."
