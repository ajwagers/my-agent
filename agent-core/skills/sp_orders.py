"""Skill: sp_orders — Summit Pine order fulfillment tracking."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineOrdersSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_orders",
            description=(
                "Track Summit Pine orders. Actions: list (optionally filter by status/channel), "
                "get (order_number), create, update_status (order_number + status + optional tracking_number). "
                "Statuses: pending, processing, shipped, delivered, refund_requested, refunded, cancelled."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_orders",
            requires_approval=False,
            private_channels=frozenset({"telegram", "cli", "mumble_owner", "web-ui"}),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update_status"],
                        "description": "Order action to perform.",
                    },
                    "order_number": {"type": "string"},
                    "status": {"type": "string",
                               "description": "pending|processing|shipped|delivered|refund_requested|refunded|cancelled"},
                    "channel": {"type": "string", "description": "shopify|local_market|subscription"},
                    "tracking_number": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "customer_email": {"type": "string"},
                    "items": {"type": "array", "description": "List of {sku, name, qty, unit_price}"},
                    "subtotal": {"type": "number"},
                    "notes": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        if not params.get("action"):
            return False, "action is required"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "list":
                query = {}
                if params.get("status"):
                    query["status"] = params["status"]
                if params.get("channel"):
                    query["channel"] = params["channel"]
                query["limit"] = params.get("limit", 20)
                resp = await client.get(f"{BRAIN_URL}/tools/list_orders", params=query)
            elif action == "get":
                resp = await client.get(f"{BRAIN_URL}/tools/get_order/{params['order_number']}")
            elif action == "create":
                body = {k: params[k] for k in (
                    "order_number", "customer_name", "customer_email",
                    "channel", "items", "subtotal", "notes"
                ) if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/create_order", json=body)
            elif action == "update_status":
                body = {"status": params["status"]}
                if params.get("tracking_number"):
                    body["tracking_number"] = params["tracking_number"]
                if params.get("notes"):
                    body["notes"] = params["notes"]
                resp = await client.put(
                    f"{BRAIN_URL}/tools/update_order_status/{params['order_number']}",
                    json=body,
                )
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No orders found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
