"""Skill: sp_promotions — Summit Pine promotions and discount code management."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPinePromotionsSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_promotions",
            description=(
                "Manage Summit Pine promotions and discount codes. "
                "Actions: create (new promotion), list (active or all promotions), "
                "get (fetch by ID), update (modify), deactivate (end a promotion early). "
                "Discount types: percent (e.g. 15% off), fixed_amount (e.g. $5 off), "
                "free_shipping, buy_x_get_y."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_promotions",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "get", "update", "deactivate"],
                        "description": "Promotion action to perform.",
                    },
                    "promotion_id": {"type": "string", "description": "Promotion UUID (required for get/update/deactivate)."},
                    "name": {"type": "string", "description": "Promotion name (required for create)."},
                    "code": {"type": "string", "description": "Discount code (optional, e.g. SPRING20)."},
                    "discount_type": {
                        "type": "string",
                        "enum": ["percent", "fixed_amount", "free_shipping", "buy_x_get_y"],
                        "description": "Type of discount (required for create).",
                    },
                    "discount_value": {"type": "number", "description": "Discount amount — percentage or dollar value (required for create)."},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD when promotion starts (required for create)."},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD when promotion expires (optional)."},
                    "applies_to": {
                        "type": "string",
                        "enum": ["all", "sku_list", "category"],
                        "description": "What the promotion applies to (default: all).",
                    },
                    "sku_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific SKUs if applies_to is sku_list.",
                    },
                    "category": {"type": "string", "description": "Product category if applies_to is category."},
                    "min_order_amount": {"type": "number", "description": "Minimum order total to qualify."},
                    "max_uses": {"type": "integer", "description": "Maximum number of times the code can be used."},
                    "notes": {"type": "string"},
                    "active_only": {"type": "boolean", "description": "True (default) to list only active/current promotions."},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if not action:
            return False, "action is required"
        if action == "create":
            for field in ("name", "discount_type", "discount_value", "start_date"):
                if not params.get(field) and params.get(field) != 0:
                    return False, f"{field} is required for create"
        if action in ("get", "update", "deactivate") and not params.get("promotion_id"):
            return False, "promotion_id is required"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "create":
                body = {k: params[k] for k in (
                    "name", "code", "discount_type", "discount_value", "start_date",
                    "applies_to", "sku_list", "category", "min_order_amount",
                    "max_uses", "end_date", "notes"
                ) if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/create_promotion", json=body)
            elif action == "list":
                active_only = params.get("active_only", True)
                resp = await client.get(f"{BRAIN_URL}/tools/list_promotions",
                                        params={"active_only": str(active_only).lower()})
            elif action == "get":
                resp = await client.get(f"{BRAIN_URL}/tools/get_promotion/{params['promotion_id']}")
            elif action == "update":
                body = {k: params[k] for k in (
                    "name", "code", "discount_type", "discount_value", "applies_to",
                    "sku_list", "category", "min_order_amount", "max_uses",
                    "start_date", "end_date", "is_active", "notes"
                ) if params.get(k) is not None}
                resp = await client.put(f"{BRAIN_URL}/tools/update_promotion/{params['promotion_id']}", json=body)
            elif action == "deactivate":
                resp = await client.post(f"{BRAIN_URL}/tools/deactivate_promotion/{params['promotion_id']}")
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No promotions found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
