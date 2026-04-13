"""Skill: sp_recipes — Summit Pine production recipe management."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineRecipesSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_recipes",
            description=(
                "Manage Summit Pine production recipes. "
                "Actions: add (create a recipe), get (fetch by ID), "
                "list (all recipes, optionally filtered by tag), "
                "update (modify an existing recipe), delete (remove a recipe). "
                "Ingredients format: [{\"name\": \"coconut oil\", \"amount\": \"200\", \"unit\": \"g\"}]"
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_recipes",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "get", "list", "update", "delete"],
                        "description": "Recipe action to perform.",
                    },
                    "recipe_id": {"type": "string", "description": "Recipe UUID (required for get/update/delete)."},
                    "name": {"type": "string", "description": "Recipe name (required for add)."},
                    "ingredients": {
                        "type": "array",
                        "description": "List of {name, amount, unit} objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "amount": {"type": "string"},
                                "unit": {"type": "string"},
                            },
                        },
                    },
                    "instructions": {"type": "string"},
                    "servings": {"type": "integer", "description": "Number of bars/units produced."},
                    "prep_time_minutes": {"type": "integer"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags like ['shampoo', 'lavender'].",
                    },
                    "tag": {"type": "string", "description": "Filter tag for list action."},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if not action:
            return False, "action is required"
        if action == "add" and not params.get("name"):
            return False, "name is required for add"
        if action in ("get", "update", "delete") and not params.get("recipe_id"):
            return False, "recipe_id is required for get/update/delete"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "add":
                body = {k: params[k] for k in ("name", "ingredients", "instructions", "servings", "prep_time_minutes", "tags") if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/add_recipe", json=body)
            elif action == "get":
                resp = await client.get(f"{BRAIN_URL}/tools/get_recipe/{params['recipe_id']}")
            elif action == "list":
                query = {}
                if params.get("tag"):
                    query["tag"] = params["tag"]
                resp = await client.get(f"{BRAIN_URL}/tools/list_recipes", params=query)
            elif action == "update":
                body = {k: params[k] for k in ("name", "ingredients", "instructions", "servings", "prep_time_minutes", "tags") if params.get(k) is not None}
                resp = await client.put(f"{BRAIN_URL}/tools/update_recipe/{params['recipe_id']}", json=body)
            elif action == "delete":
                resp = await client.delete(f"{BRAIN_URL}/tools/delete_recipe/{params['recipe_id']}")
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No recipes found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
