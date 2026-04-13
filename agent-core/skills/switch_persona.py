"""
SwitchPersonaSkill — LLM-callable skill to switch the active agent persona.
"""

from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class SwitchPersonaSkill(SkillBase):
    """Switch the active agent persona for the current user."""

    def __init__(self, persona_registry):
        self._registry = persona_registry

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="switch_persona",
            description=(
                "Switch to a different agent persona. Use when the user says 'switch to X', "
                "'use the X agent', 'talk to me as X', or 'go back to default'. "
                "The new persona takes effect on the next message. "
                "Use list_personas first if unsure of the exact name."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="switch_persona",
            requires_approval=False,
            max_calls_per_turn=2,
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Persona slug to switch to. Use 'default' to return to the main agent. "
                            "e.g. 'summit_pine', 'health_coach', 'default'"
                        ),
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
        user_id = params.pop("_user_id", "default")
        params.pop("_persona", None)
        name = params["name"]

        persona = self._registry.get(name)
        if not persona:
            available = [p.name for p in self._registry.list_all()]
            return {"error": f"Unknown persona '{name}'. Available: {', '.join(available)}"}

        self._registry.set_session(user_id, name)
        return {"switched_to": name, "display_name": persona.display_name}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[switch_persona] {result['error']}"
        display = result.get("display_name", result.get("switched_to", "?"))
        name = result.get("switched_to", "?")
        if name == "default":
            return f"✅ Switched back to the default agent. Your next message will use the main AI agent."
        return f"✅ Switched to **{display}**. Your next message will use that agent."
