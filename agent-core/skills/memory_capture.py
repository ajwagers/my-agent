"""Skill: capture_thought — save a thought/note to Open Brain."""
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class MemoryCaptureSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="capture_thought",
            description=(
                "Save a thought, note, fact, or memory to the persistent brain database. "
                "Use for anything the user wants to remember across sessions, or important "
                "observations worth retaining. The brain automatically extracts topics, "
                "people mentioned, action items, and dates."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="memory_capture",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The thought, note, or fact to save.",
                    },
                },
                "required": ["content"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        if not params.get("content", "").strip():
            return False, "content must not be empty"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        content = params["content"].strip()
        source = params.get("_channel", "agent")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BRAIN_URL}/tools/capture_thought",
                json={"content": content, "source": source},
            )
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict):
            meta = result.get("metadata", {})
            topics = meta.get("topics", [])
            thought_type = meta.get("type", "")
            topics_str = ", ".join(topics) if topics else "general"
            return f"Saved to memory. Type: {thought_type}. Topics: {topics_str}."
        return "Saved to memory."
