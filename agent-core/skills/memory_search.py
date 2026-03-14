"""Skill: search_thoughts — explicit semantic search over brain memory."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class MemorySearchSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="search_thoughts",
            description=(
                "Search the persistent brain memory for relevant past thoughts, notes, or facts. "
                "Use when the user asks 'do you remember', 'what did I say about', or references "
                "previous conversations. Returns semantically matched results."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="memory_search",
            requires_approval=False,
            private_channels=frozenset({"telegram", "cli"}),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in memory.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 10).",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
            max_calls_per_turn=3,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        if not params.get("query", "").strip():
            return False, "query must not be empty"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BRAIN_URL}/tools/search_thoughts",
                json={
                    "query": params["query"],
                    "limit": params.get("limit", 10),
                    "threshold": 0.45,
                },
            )
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if not result:
            return "No matching memories found."
        lines = [f"Found {len(result)} memory match(es):"]
        for item in result[:8]:
            sim = item.get("similarity", 0)
            ts = item.get("created_at", "")[:10]
            lines.append(f"- [{ts}] {item['content'][:300]} (similarity: {sim:.2f})")
        return "\n".join(lines)
