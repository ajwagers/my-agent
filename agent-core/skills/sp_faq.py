"""Skill: sp_faq — Summit Pine FAQ search and management."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineFAQSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_faq",
            description=(
                "Search or manage the Summit Pine customer support FAQ. "
                "Actions: search (query), list (optional category), add (question+answer+category). "
                "Categories: usage, ingredients, guarantee, ordering, shipping, production, science. "
                "Always check guardrail field — 'no_medical_advice' entries must refer to dermatologist."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_faq",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "list", "add"],
                        "description": "FAQ action.",
                    },
                    "query": {"type": "string", "description": "Search query for FAQ lookup."},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "category": {
                        "type": "string",
                        "description": "usage|ingredients|guarantee|ordering|shipping|production|science",
                    },
                    "guardrail": {"type": "string", "description": "e.g. no_medical_advice"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        if not params.get("action"):
            return False, "action is required"
        if params["action"] == "search" and not params.get("query"):
            return False, "query required for search"
        if params["action"] == "add":
            for f in ("question", "answer", "category"):
                if not params.get(f):
                    return False, f"{f} required for add"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "search":
                resp = await client.post(f"{BRAIN_URL}/tools/search_faq", json={
                    "query": params["query"],
                    "limit": params.get("limit", 5),
                    "threshold": 0.45,
                })
            elif action == "list":
                query = {}
                if params.get("category"):
                    query["category"] = params["category"]
                resp = await client.get(f"{BRAIN_URL}/tools/list_faq_by_category", params=query)
            elif action == "add":
                resp = await client.post(f"{BRAIN_URL}/tools/add_faq_entry", json={
                    "question": params["question"],
                    "answer": params["answer"],
                    "category": params["category"],
                    "guardrail": params.get("guardrail"),
                })
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No FAQ entries found."
            lines = []
            for item in result[:5]:
                guardrail = item.get("guardrail", "")
                lines.append(f"Q: {item['question']}")
                lines.append(f"A: {item['answer']}")
                if guardrail:
                    lines.append(f"[Guardrail: {guardrail}]")
                lines.append("")
            return "\n".join(lines)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:1000]
        return str(result)[:1000]
