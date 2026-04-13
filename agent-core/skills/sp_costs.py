"""Skill: sp_costs — Summit Pine expense tracking, COGS, and P&L."""
import json
import os
from typing import Any, Dict, Tuple

import httpx

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")


class SummitPineCostsSkill(SkillBase):

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="sp_costs",
            description=(
                "Track Summit Pine costs, COGS, and P&L. "
                "Actions: log_expense (record a purchase/expense), "
                "list_expenses (filter by date range or category), "
                "expense_summary (totals by category for a month), "
                "batch_cogs (ingredient cost breakdown for a production batch), "
                "profit_summary (revenue - expenses for a calendar month). "
                "Categories: ingredients, packaging, equipment, shipping, marketing, other."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="sp_costs",
            requires_approval=False,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "log_expense", "list_expenses",
                            "expense_summary", "batch_cogs", "profit_summary",
                        ],
                        "description": "Cost action to perform.",
                    },
                    "description": {"type": "string", "description": "Expense description (required for log_expense)."},
                    "amount": {"type": "number", "description": "Expense amount in USD (required for log_expense)."},
                    "category": {
                        "type": "string",
                        "description": "ingredients|packaging|equipment|shipping|marketing|other",
                    },
                    "expense_date": {"type": "string", "description": "YYYY-MM-DD (defaults to today)."},
                    "supplier": {"type": "string"},
                    "sku": {"type": "string", "description": "Inventory SKU if expense is for a specific item."},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "receipt_ref": {"type": "string", "description": "Invoice or receipt number."},
                    "notes": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD filter start for list_expenses."},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD filter end for list_expenses."},
                    "year": {"type": "integer", "description": "Year for expense_summary or profit_summary."},
                    "month": {"type": "integer", "description": "Month (1-12) for expense_summary or profit_summary."},
                    "batch_number": {"type": "string", "description": "Batch number for batch_cogs."},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["action"],
            },
            max_calls_per_turn=5,
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        action = params.get("action", "")
        if not action:
            return False, "action is required"
        if action == "log_expense":
            if not params.get("description"):
                return False, "description is required for log_expense"
            if params.get("amount") is None:
                return False, "amount is required for log_expense"
        if action == "batch_cogs" and not params.get("batch_number"):
            return False, "batch_number is required for batch_cogs"
        if action == "profit_summary":
            if not params.get("year") or not params.get("month"):
                return False, "year and month are required for profit_summary"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        params.pop("_user_id", None)
        params.pop("_persona", None)
        action = params["action"]
        async with httpx.AsyncClient(timeout=20) as client:
            if action == "log_expense":
                body = {k: params[k] for k in (
                    "description", "amount", "category", "expense_date",
                    "supplier", "sku", "quantity", "unit", "receipt_ref", "notes"
                ) if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/log_expense", json=body)
            elif action == "list_expenses":
                body = {k: params[k] for k in ("start_date", "end_date", "category", "limit") if params.get(k) is not None}
                resp = await client.post(f"{BRAIN_URL}/tools/list_expenses", json=body)
            elif action == "expense_summary":
                query = {}
                if params.get("year"):
                    query["year"] = params["year"]
                if params.get("month"):
                    query["month"] = params["month"]
                resp = await client.get(f"{BRAIN_URL}/tools/expense_summary", params=query)
            elif action == "batch_cogs":
                resp = await client.get(f"{BRAIN_URL}/tools/batch_cogs/{params['batch_number']}")
            elif action == "profit_summary":
                resp = await client.get(f"{BRAIN_URL}/tools/profit_summary",
                                        params={"year": params["year"], "month": params["month"]})
            else:
                return {"error": f"Unknown action: {action}"}
            resp.raise_for_status()
            return resp.json()

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, list):
            if not result:
                return "No expenses found."
            return json.dumps(result, indent=2)[:3000]
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return json.dumps(result, indent=2)[:2000]
        return str(result)[:2000]
