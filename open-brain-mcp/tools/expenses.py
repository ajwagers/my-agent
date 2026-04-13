"""Summit Pine expense tracking and COGS / P&L calculations."""
from datetime import date
from typing import Optional

from db import get_pool


async def log_expense(
    description: str,
    amount: float,
    category: str = "ingredients",
    expense_date: str = None,
    supplier: str = None,
    sku: str = None,
    quantity: float = None,
    unit: str = None,
    receipt_ref: str = None,
    notes: str = None,
) -> dict:
    pool = await get_pool()
    ed = date.fromisoformat(expense_date) if expense_date else date.today()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO sp_expenses
               (expense_date, category, description, supplier, amount,
                sku, quantity, unit, receipt_ref, notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
               RETURNING id, expense_date, category, amount""",
            ed, category, description, supplier, amount,
            sku, quantity, unit, receipt_ref, notes,
        )
    return {
        "id": str(row["id"]),
        "expense_date": row["expense_date"].isoformat(),
        "category": row["category"],
        "amount": float(row["amount"]),
        "recorded": True,
    }


async def list_expenses(
    start_date: str = None,
    end_date: str = None,
    category: str = None,
    limit: int = 50,
) -> list[dict]:
    pool = await get_pool()
    conditions, args = [], []
    if start_date:
        args.append(date.fromisoformat(start_date))
        conditions.append(f"expense_date >= ${len(args)}")
    if end_date:
        args.append(date.fromisoformat(end_date))
        conditions.append(f"expense_date <= ${len(args)}")
    if category:
        args.append(category)
        conditions.append(f"category = ${len(args)}")
    args.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM sp_expenses {where} ORDER BY expense_date DESC LIMIT ${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_expense_row(r) for r in rows]


async def expense_summary(year: int = None, month: int = None) -> dict:
    """Total expenses grouped by category for the given month (or all time)."""
    pool = await get_pool()
    if year and month:
        sql = """SELECT category, SUM(amount) AS total
                 FROM sp_expenses
                 WHERE EXTRACT(year FROM expense_date) = $1
                   AND EXTRACT(month FROM expense_date) = $2
                 GROUP BY category ORDER BY total DESC"""
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, year, month)
    else:
        sql = """SELECT category, SUM(amount) AS total
                 FROM sp_expenses GROUP BY category ORDER BY total DESC"""
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
    by_category = {r["category"]: float(r["total"]) for r in rows}
    return {
        "period": f"{year}-{month:02d}" if year and month else "all_time",
        "by_category": by_category,
        "total": sum(by_category.values()),
    }


async def batch_cogs(batch_number: str) -> dict:
    """Compute ingredient cost for a production batch using ingredients_used + unit_cost."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow(
            "SELECT * FROM production_batches WHERE batch_number=$1", batch_number
        )
        if not batch:
            return {"error": f"Batch {batch_number!r} not found"}

        ingredients_used = batch["ingredients_used"] or []
        line_items = []
        total_cost = 0.0

        for ing in ingredients_used:
            sku = ing.get("sku")
            amount = float(ing.get("amount_g", 0))
            if not sku or amount == 0:
                continue
            item = await conn.fetchrow(
                "SELECT name, unit_cost, unit FROM inventory_items WHERE sku=$1", sku
            )
            if item and item["unit_cost"]:
                line_cost = amount * float(item["unit_cost"])
                total_cost += line_cost
                line_items.append({
                    "sku": sku,
                    "name": item["name"],
                    "amount_g": amount,
                    "unit_cost_per_g": float(item["unit_cost"]),
                    "line_cost": round(line_cost, 4),
                })
            else:
                line_items.append({
                    "sku": sku,
                    "amount_g": amount,
                    "line_cost": None,
                    "note": "unit_cost not set",
                })

    qty = batch["quantity_produced"]
    return {
        "batch_number": batch_number,
        "product_type": batch["product_type"],
        "quantity_produced": qty,
        "ingredient_cost_total": round(total_cost, 2),
        "cost_per_unit": round(total_cost / qty, 4) if qty else None,
        "line_items": line_items,
    }


async def profit_summary(year: int, month: int) -> dict:
    """Revenue − COGS − expenses for a calendar month."""
    pool = await get_pool()

    # Revenue
    revenue_rows = await pool.fetch(
        """SELECT COALESCE(SUM(total), 0) AS revenue, COUNT(*) AS order_count
           FROM orders
           WHERE EXTRACT(year FROM created_at) = $1
             AND EXTRACT(month FROM created_at) = $2
             AND status NOT IN ('refunded', 'cancelled')""",
        year, month,
    )
    revenue = float(revenue_rows[0]["revenue"]) if revenue_rows else 0.0
    order_count = int(revenue_rows[0]["order_count"]) if revenue_rows else 0

    # Expenses (cash-based)
    expense_rows = await pool.fetch(
        """SELECT COALESCE(SUM(amount), 0) AS expenses
           FROM sp_expenses
           WHERE EXTRACT(year FROM expense_date) = $1
             AND EXTRACT(month FROM expense_date) = $2""",
        year, month,
    )
    expenses = float(expense_rows[0]["expenses"]) if expense_rows else 0.0

    gross_profit = revenue - expenses
    margin_pct = round(gross_profit / revenue * 100, 1) if revenue else None

    return {
        "period": f"{year}-{month:02d}",
        "revenue": round(revenue, 2),
        "order_count": order_count,
        "expenses": round(expenses, 2),
        "gross_profit": round(gross_profit, 2),
        "margin_pct": margin_pct,
    }


def _expense_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "expense_date": r["expense_date"].isoformat(),
        "category": r["category"],
        "description": r["description"],
        "supplier": r["supplier"],
        "amount": float(r["amount"]),
        "sku": r["sku"],
        "quantity": float(r["quantity"]) if r["quantity"] else None,
        "unit": r["unit"],
        "receipt_ref": r["receipt_ref"],
        "notes": r["notes"],
    }
