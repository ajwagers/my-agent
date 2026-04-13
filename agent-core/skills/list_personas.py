"""
ListPersonasSkill — LLM-callable skill to list available agent personas.
"""

from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class ListPersonasSkill(SkillBase):
    """List all available agent personas and the currently active one."""

    def __init__(self, persona_registry):
        self._registry = persona_registry

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="list_personas",
            description=(
                "List all available agent personas. Use when the user asks 'what agents are available', "
                "'show me personas', 'what can I switch to', or similar."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="list_personas",
            requires_approval=False,
            max_calls_per_turn=3,
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        user_id = params.pop("_user_id", "default")
        params.pop("_persona", None)
        active = self._registry.get_session(user_id)
        personas = self._registry.list_all()
        return {
            "active": active,
            "personas": [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "is_builtin": p.is_builtin,
                    "skills": p.allowed_skills,
                }
                for p in personas
            ],
        }

    def sanitize_output(self, result: Any) -> str:
        active = result.get("active", "default")
        personas = result.get("personas", [])
        lines = ["**Available agents:**"]
        for p in personas:
            marker = " ◀ active" if p["name"] == active else ""
            tag = " (built-in)" if p["is_builtin"] else ""
            lines.append(f"• **{p['display_name']}** — `/switch {p['name']}`{tag}{marker}")
        lines.append(f"\nType `/switch <name>` to switch agents.")
        return "\n".join(lines)
