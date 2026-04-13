"""
CreatePersonaSkill — LLM-callable skill to create a new named agent persona.
"""

import re
from typing import Any, Dict, Tuple

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


class CreatePersonaSkill(SkillBase):
    """Create a new named agent persona with a custom personality and optional skill restriction."""

    def __init__(self, persona_registry):
        self._registry = persona_registry

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="create_persona",
            description=(
                "Create a new named agent persona. Use when the user asks to create a new agent, "
                "assistant, or persona with a specific focus or personality. "
                "Craft a detailed system_prompt describing the persona's role, tone, and focus area. "
                "Optionally restrict it to a subset of skills."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="create_persona",
            requires_approval=False,
            max_calls_per_turn=2,
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Slug identifier: lowercase letters, digits, underscores, 2–32 chars. "
                            "e.g. 'health_coach'"
                        ),
                    },
                    "display_name": {
                        "type": "string",
                        "description": "Human-readable name. e.g. 'Health & Fitness Coach'",
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": (
                            "System prompt overlay for this persona. "
                            "Describe its role, personality, focus area, and any constraints."
                        ),
                    },
                    "allowed_skills": {
                        "type": "string",
                        "description": (
                            "Comma-separated skill names to restrict this persona to. "
                            "Omit or leave blank to allow all skills."
                        ),
                    },
                },
                "required": ["name", "display_name", "system_prompt"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        name = params.get("name", "")
        if not re.match(r"^[a-z][a-z0-9_]{1,31}$", name):
            return False, "name must be 2–32 chars: lowercase letters, digits, underscores"
        if not str(params.get("display_name", "")).strip():
            return False, "display_name is required"
        if not str(params.get("system_prompt", "")).strip():
            return False, "system_prompt is required"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        name = params["name"]
        display_name = params["display_name"].strip()
        system_prompt = params["system_prompt"].strip()
        allowed_str = params.get("allowed_skills", "") or ""
        allowed = [s.strip() for s in allowed_str.split(",") if s.strip()] or None
        try:
            persona = self._registry.create(name, display_name, system_prompt, allowed)
            return {"name": persona.name, "display_name": persona.display_name}
        except ValueError as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[create_persona] Error: {result['error']}"
        name = result.get("name", "?")
        display = result.get("display_name", name)
        return f"✅ Created agent persona '{display}'. Switch to it with: /switch {name}"
