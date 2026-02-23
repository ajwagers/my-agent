"""
Recall skill — semantic search over long-term agent memory.
"""

import time
from typing import Any, Dict, List, Tuple

from memory import MemoryStore
from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


def _format_age(seconds: float) -> str:
    """Format elapsed seconds into a human-readable age string."""
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d"
    weeks = days / 7
    if weeks < 4.3:
        return f"{int(weeks)}w"
    months = days / 30
    return f"{int(months)}mo"


class RecallSkill(SkillBase):
    """Search long-term agent memory for relevant stored facts or observations."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="recall",
            description=(
                "Search long-term memory for stored facts, observations, or preferences. "
                "Use this to retrieve information remembered from previous conversations."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="recall",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in memory (max 500 chars).",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10, default 5).",
                    },
                },
                "required": ["query"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        query = params.get("query", "")
        if not isinstance(query, str):
            return False, "Parameter 'query' must be a string"
        if not query.strip():
            return False, "Parameter 'query' must not be empty"
        if len(query) > 500:
            return False, "Parameter 'query' must be under 500 characters"

        n_results = params.get("n_results", 5)
        if not isinstance(n_results, int) or isinstance(n_results, bool):
            return False, "Parameter 'n_results' must be an integer"
        if n_results < 1 or n_results > 10:
            return False, "Parameter 'n_results' must be between 1 and 10"

        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        user_id = params.pop("_user_id", "default")
        query = params["query"]
        n_results = params.get("n_results", 5)

        try:
            store = MemoryStore()
            entries = store.search(query=query, user_id=user_id, n_results=n_results)
            now = time.time()
            formatted: List[Dict] = []
            for entry in entries:
                memory_type = entry.get("type", "fact")
                timestamp = entry.get("timestamp", now)
                age = _format_age(now - timestamp)
                content = entry.get("content", "")
                formatted.append({"type": memory_type, "age": age, "content": content})
            return formatted
        except Exception as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[recall] {result['error']}"
        if isinstance(result, list):
            if not result:
                return "No memories found."
            lines = []
            for i, entry in enumerate(result, 1):
                memory_type = entry.get("type", "fact")
                age = entry.get("age", "?")
                content = entry.get("content", "")
                lines.append(f"{i}. [{memory_type}, {age}] {content}")
            return "\n".join(lines)
        return str(result)
