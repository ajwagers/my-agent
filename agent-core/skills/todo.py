"""Skill: todo — personal to-do list and shopping list management."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata, PRIVATE_CHANNELS

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class TodoSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="todo",
            description=(
                "Manage a personal to-do list and shopping list. "
                "Actions: add (new item), list (show pending items), "
                "complete (mark done by id), delete (remove by id). "
                "Categories: task (things to do), purchase (things to buy), errand (places to go). "
                "Priority: low | medium | high."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="default",
            requires_approval=False,
            private_channels=PRIVATE_CHANNELS,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "complete", "delete"],
                        "description": "add: create item; list: show pending; complete: mark done; delete: remove",
                    },
                    "text": {
                        "type": "string",
                        "description": "Item description (required for add)",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["task", "purchase", "errand"],
                        "description": "task=things to do, purchase=things to buy, errand=places to go. Default: task",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Priority level. Default: medium",
                    },
                    "id": {
                        "type": "integer",
                        "description": "Todo ID for complete or delete actions",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "done"],
                        "description": "Filter by status for list action. Default: pending",
                    },
                },
                "required": ["action"],
            },
            max_calls_per_turn=10,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action")
        if not action:
            return False, "action is required"
        if action == "add" and not params.get("text"):
            return False, "text is required for add"
        if action in ("complete", "delete") and params.get("id") is None:
            return False, "id is required for complete/delete"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        action = params["action"]
        user_id = params.get("_user_id", "owner")
        async with httpx.AsyncClient(timeout=10) as client:
            if action == "add":
                resp = await client.post(f"{BRAIN_URL}/tools/todo/add", json={
                    "text": params["text"],
                    "category": params.get("category", "task"),
                    "priority": params.get("priority", "medium"),
                    "user_id": user_id,
                })
                resp.raise_for_status()
                data = resp.json()
                return {"added": f"#{data['id']}: {data['text']} [{data['category']}, {data['priority']}]"}

            elif action == "list":
                status = params.get("status", "pending")
                category = params.get("category")
                url = f"{BRAIN_URL}/tools/todo/list"
                query = {"status": status, "user_id": user_id}
                if category:
                    query["category"] = category
                resp = await client.get(url, params=query)
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    return {"items": [], "summary": f"No {status} items."}
                lines = []
                for item in items:
                    lines.append(
                        f"#{item['id']} [{item['category']}/{item['priority']}] {item['text']}"
                    )
                return {"count": len(items), "items": lines}

            elif action == "complete":
                resp = await client.post(f"{BRAIN_URL}/tools/todo/complete/{params['id']}")
                resp.raise_for_status()
                return resp.json()

            elif action == "delete":
                resp = await client.delete(f"{BRAIN_URL}/tools/todo/{params['id']}")
                resp.raise_for_status()
                return resp.json()

            else:
                return {"error": f"Unknown action: {action}"}

    def sanitize_output(self, result: Any) -> str:
        text = json.dumps(result) if not isinstance(result, str) else result
        return text[:2000]
