"""Summit Pine inventory management — raw materials, finished goods, batches."""
import json
from datetime import date, timedelta

from db import get_pool
from embeddings import embed, vec_to_str


# ── Inventory items ───────────────────────────────────────────────────────────

async def add_inventory_item(
    sku: str, name: str, category: str, unit: str,
    quantity_on_hand: float = 0, reorder_threshold: float = None,
    reorder_quantity: float = None, unit_cost: float = None,
    supplier: str = None, supplier_lead_days: int = None,
    is_critical: bool = False, notes: str = None,
) -> dict:
    pool = await get_pool()
    emb = await embed(f"{category} {name}: {notes or ''}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO inventory_items
               (sku, name, category, unit, quantity_on_hand, reorder_threshold,
                reorder_quantity, unit_cost, supplier, supplier_lead_days,
                is_critical, notes, embedding)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::vector)
               RETURNING id, sku, name""",
            sku, name, category, unit, quantity_on_hand, reorder_threshold,
            reorder_quantity, unit_cost, supplier, supplier_lead_days,
            is_critical, notes, vec_to_str(emb),
        )
    return {"id": str(row["id"]), "sku": row["sku"], "name": row["name"]}


async def update_inventory(sku: str, quantity_on_hand: float = None,
                            unit_cost: float = None, notes: str = None,
                            reorder_threshold: float = None) -> dict:
    pool = await get_pool()
    sets, args = [], []
    for field, val in [("quantity_on_hand", quantity_on_hand), ("unit_cost", unit_cost),
                       ("notes", notes), ("reorder_threshold", reorder_threshold)]:
        if val is not None:
            args.append(val)
            sets.append(f"{field} = ${len(args)}")
    if not sets:
        return {"error": "Nothing to update"}
    args.append(sku)
    sql = f"UPDATE inventory_items SET {', '.join(sets)}, updated_at=NOW() WHERE sku=${len(args)} RETURNING sku, name, quantity_on_hand"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": f"SKU {sku!r} not found"}
    return {"sku": row["sku"], "name": row["name"],
            "quantity_on_hand": float(row["quantity_on_hand"]), "updated": True}


async def bulk_update_quantities(updates: list) -> dict:
    """Update quantity_on_hand for multiple SKUs in one call.

    updates: list of {sku, quantity} dicts.
    Returns {updated: [...], not_found: [...], errors: [...]}.
    """
    pool = await get_pool()
    updated, not_found, errors = [], [], []
    async with pool.acquire() as conn:
        for item in updates:
            sku = item.get("sku")
            qty = item.get("quantity")
            if sku is None or qty is None:
                errors.append({"entry": item, "error": "sku and quantity required"})
                continue
            try:
                row = await conn.fetchrow(
                    "UPDATE inventory_items SET quantity_on_hand=$1, updated_at=NOW() "
                    "WHERE sku=$2 RETURNING sku, name, quantity_on_hand",
                    float(qty), sku,
                )
                if row:
                    updated.append({"sku": row["sku"], "name": row["name"],
                                    "quantity_on_hand": float(row["quantity_on_hand"])})
                else:
                    not_found.append(sku)
            except Exception as e:
                errors.append({"sku": sku, "error": str(e)})
    return {"updated": updated, "not_found": not_found, "errors": errors}


async def get_inventory_item(sku: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM inventory_items WHERE sku=$1", sku
        )
    if not row:
        return {"error": f"SKU {sku!r} not found"}
    return _item_row(row)


async def list_inventory(category: str = None) -> list[dict]:
    pool = await get_pool()
    if category:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM inventory_items WHERE category=$1 ORDER BY name", category
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM inventory_items ORDER BY category, name"
            )
    return [_item_row(r) for r in rows]


async def list_low_stock() -> list[dict]:
    """Items at or below reorder_threshold. Criticals always included."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM inventory_items
               WHERE reorder_threshold IS NOT NULL
                 AND quantity_on_hand <= reorder_threshold
               ORDER BY is_critical DESC, quantity_on_hand ASC"""
        )
    result = [_item_row(r) for r in rows]
    # Annotate urgency
    for item in result:
        lead = item.get("supplier_lead_days") or 5
        days_of_stock = 0
        if item["reorder_threshold"] and item["reorder_threshold"] > 0:
            ratio = item["quantity_on_hand"] / item["reorder_threshold"]
            days_of_stock = int(ratio * 30)  # rough estimate
        item["days_of_stock_est"] = days_of_stock
        item["order_by"] = (date.today() + timedelta(days=max(0, days_of_stock - lead))).isoformat()
    return result


# ── Production batches ────────────────────────────────────────────────────────

async def record_production_batch(
    batch_number: str, product_type: str, batch_date: str,
    quantity_produced: int, ingredients_used: list = None, qc_notes: str = None,
) -> dict:
    pool = await get_pool()
    bd = date.fromisoformat(batch_date)
    cure_days = 42 if product_type == "shampoo_bar" else 1  # 6 weeks vs 24hr
    cure_complete = bd + timedelta(days=cure_days)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO production_batches
               (batch_number, product_type, batch_date, quantity_produced,
                cure_complete_date, ingredients_used, qc_notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7)
               RETURNING id, batch_number, status, cure_complete_date""",
            batch_number, product_type, bd, quantity_produced,
            cure_complete, json.dumps(ingredients_used or []), qc_notes,
        )
    return {
        "id": str(row["id"]),
        "batch_number": row["batch_number"],
        "status": row["status"],
        "cure_complete_date": row["cure_complete_date"].isoformat(),
    }


async def update_batch_status(batch_number: str, status: str,
                               ph_test_result: float = None, qc_notes: str = None) -> dict:
    pool = await get_pool()
    sets = ["status=$1", "updated_at=NOW()"]
    args = [status]
    if ph_test_result is not None:
        args.append(ph_test_result)
        sets.append(f"ph_test_result=${len(args)}")
    if qc_notes:
        args.append(qc_notes)
        sets.append(f"qc_notes=${len(args)}")
        args.append(batch_number)
    else:
        args.append(batch_number)
    sql = f"UPDATE production_batches SET {', '.join(sets)} WHERE batch_number=${len(args)} RETURNING batch_number, status"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": f"Batch {batch_number!r} not found"}
    return {"batch_number": row["batch_number"], "status": row["status"], "updated": True}


async def list_batches(status: str = None) -> list[dict]:
    pool = await get_pool()
    if status:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM production_batches WHERE status=$1 ORDER BY batch_date DESC", status
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM production_batches ORDER BY batch_date DESC LIMIT 50"
            )
    return [_batch_row(r) for r in rows]


async def get_batch_status(batch_number: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM production_batches WHERE batch_number=$1", batch_number
        )
    if not row:
        return {"error": f"Batch {batch_number!r} not found"}
    return _batch_row(row)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _item_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "sku": r["sku"],
        "name": r["name"],
        "category": r["category"],
        "unit": r["unit"],
        "quantity_on_hand": float(r["quantity_on_hand"]),
        "reorder_threshold": float(r["reorder_threshold"]) if r["reorder_threshold"] else None,
        "reorder_quantity": float(r["reorder_quantity"]) if r["reorder_quantity"] else None,
        "unit_cost": float(r["unit_cost"]) if r["unit_cost"] else None,
        "supplier": r["supplier"],
        "supplier_lead_days": r["supplier_lead_days"],
        "is_critical": r["is_critical"],
        "notes": r["notes"],
    }


def _batch_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "batch_number": r["batch_number"],
        "product_type": r["product_type"],
        "batch_date": r["batch_date"].isoformat(),
        "quantity_produced": r["quantity_produced"],
        "cure_complete_date": r["cure_complete_date"].isoformat() if r["cure_complete_date"] else None,
        "status": r["status"],
        "ph_test_result": float(r["ph_test_result"]) if r["ph_test_result"] else None,
        "qc_notes": r["qc_notes"],
    }
