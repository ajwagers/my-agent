"""Skill: sp_inventory — Summit Pine inventory management."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineInventorySkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_inventory",
            description=(
                "Manage Summit Pine inventory. Actions: list_all, list_low_stock, "
                "get_item (sku), update_quantity (sku + quantity), "
                "bulk_update (updates=[{sku, quantity}, ...] — update many at once), "
                "list_batches, get_batch (batch_number), record_batch, update_batch_status. "
                "Use bulk_update when loading or refreshing a full inventory count. "
                "Use list_low_stock to check what needs reordering."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_inventory",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_all", "list_low_stock", "get_item",
                            "update_quantity", "bulk_update",
                            "list_batches", "get_batch",
                            "record_batch", "update_batch_status",
                        ],
                        "description": "Inventory action to perform.",
                    },
                    "sku": {"type": "string", "description": "Product SKU (for single-item actions)."},
                    "quantity": {"type": "number", "description": "New quantity on hand (for update_quantity)."},
                    "updates": {
                        "type": "array",
                        "description": "List of {sku, quantity} objects for bulk_update.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "number"},
                            },
                            "required": ["sku", "quantity"],
                        },
                    },
                    "category": {"type": "string", "description": "Filter by category: raw_material, finished_good, packaging."},
                    "batch_number": {"type": "string"},
                    "product_type": {"type": "string", "description": "shampoo_bar or conditioner_bar"},
                    "batch_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "quantity_produced": {"type": "integer"},
                    "status": {"type": "string", "description": "Batch status: curing, cured, in_stock, depleted"},
                    "ph_test_result": {"type": "number"},
                    "qc_notes": {"type": "string"},
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
            if action == "list_all":
                resp = await client.get(f"{BRAIN_URL}/tools/list_inventory",
                                        params={"category": params.get("category")} if params.get("category") else {})
            elif action == "list_low_stock":
                resp = await client.get(f"{BRAIN_URL}/tools/list_low_stock")
            elif action == "get_item":
                resp = await client.get(f"{BRAIN_URL}/tools/get_inventory_item/{params['sku']}")
            elif action == "update_quantity":
                resp = await client.put(
                    f"{BRAIN_URL}/tools/update_inventory/{params['sku']}",
                    json={"quantity_on_hand": params["quantity"]},
                )
            elif action == "bulk_update":
                updates = params.get("updates") or []
                resp = await client.post(
                    f"{BRAIN_URL}/tools/bulk_update_inventory",
                    json=updates,
                )
            elif action == "list_batches":
                resp = await client.get(f"{BRAIN_URL}/tools/list_batches",
                                        params={"status": params["status"]} if params.get("status") else {})
            elif action == "get_batch":
                resp = await client.get(f"{BRAIN_URL}/tools/get_batch_status/{params['batch_number']}")
            elif action == "record_batch":
                resp = await client.post(f"{BRAIN_URL}/tools/record_production_batch", json={
                    "batch_number": params["batch_number"],
                    "product_type": params["product_type"],
                    "batch_date": params["batch_date"],
                    "quantity_produced": params["quantity_produced"],
                    "qc_notes": params.get("qc_notes"),
                })
            elif action == "update_batch_status":
                resp = await client.put(
                    f"{BRAIN_URL}/tools/update_batch_status/{params['batch_number']}",
                    json={k: params[k] for k in ("status", "ph_test_result", "qc_notes") if params.get(k) is not None},
                )
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No items found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
