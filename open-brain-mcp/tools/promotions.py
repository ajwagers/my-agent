"""Summit Pine promotions and discount code management."""
from datetime import date

from db import get_pool


async def create_promotion(
    name: str,
    discount_type: str,
    discount_value: float,
    start_date: str,
    code: str = None,
    applies_to: str = "all",
    sku_list: list = None,
    category: str = None,
    min_order_amount: float = None,
    max_uses: int = None,
    end_date: str = None,
    notes: str = None,
) -> dict:
    pool = await get_pool()
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date) if end_date else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO sp_promotions
               (name, code, discount_type, discount_value, applies_to,
                sku_list, category, min_order_amount, max_uses,
                start_date, end_date, notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
               RETURNING id, name, discount_type, discount_value, start_date""",
            name, code, discount_type, discount_value, applies_to,
            sku_list or [], category, min_order_amount, max_uses,
            sd, ed, notes,
        )
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "discount_type": row["discount_type"],
        "discount_value": float(row["discount_value"]),
        "start_date": row["start_date"].isoformat(),
        "created": True,
    }


async def list_promotions(active_only: bool = True) -> list[dict]:
    pool = await get_pool()
    if active_only:
        sql = """SELECT * FROM sp_promotions
                 WHERE is_active=TRUE
                   AND start_date <= CURRENT_DATE
                   AND (end_date IS NULL OR end_date >= CURRENT_DATE)
                 ORDER BY start_date DESC"""
    else:
        sql = "SELECT * FROM sp_promotions ORDER BY start_date DESC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [_row(r) for r in rows]


async def get_promotion(promotion_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM sp_promotions WHERE id=$1", promotion_id)
    if not row:
        return {"error": f"Promotion {promotion_id!r} not found"}
    return _row(row)


async def update_promotion(promotion_id: str, **kwargs) -> dict:
    pool = await get_pool()
    allowed = {"name", "code", "discount_type", "discount_value", "applies_to",
               "sku_list", "category", "min_order_amount", "max_uses",
               "start_date", "end_date", "is_active", "notes"}
    sets, args = [], []
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            if k in ("start_date", "end_date") and isinstance(v, str):
                v = date.fromisoformat(v)
            args.append(v); sets.append(f"{k}=${len(args)}")
    if not sets:
        return {"error": "Nothing to update"}
    args.append(promotion_id)
    sql = f"UPDATE sp_promotions SET {', '.join(sets)}, updated_at=NOW() WHERE id=${len(args)} RETURNING id, name"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": f"Promotion {promotion_id!r} not found"}
    return {"id": str(row["id"]), "name": row["name"], "updated": True}


async def deactivate_promotion(promotion_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE sp_promotions SET is_active=FALSE, updated_at=NOW() WHERE id=$1 RETURNING id, name",
            promotion_id,
        )
    if not row:
        return {"error": f"Promotion {promotion_id!r} not found"}
    return {"id": str(row["id"]), "name": row["name"], "deactivated": True}


async def increment_uses(promotion_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE sp_promotions
               SET uses_count = uses_count + 1, updated_at=NOW()
               WHERE id=$1
                 AND (max_uses IS NULL OR uses_count < max_uses)
               RETURNING id, name, uses_count, max_uses""",
            promotion_id,
        )
    if not row:
        return {"error": "Promotion not found or max uses reached"}
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "uses_count": row["uses_count"],
        "max_uses": row["max_uses"],
    }


def _row(r) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "code": r["code"],
        "discount_type": r["discount_type"],
        "discount_value": float(r["discount_value"]),
        "applies_to": r["applies_to"],
        "sku_list": list(r["sku_list"]) if r["sku_list"] else [],
        "category": r["category"],
        "min_order_amount": float(r["min_order_amount"]) if r["min_order_amount"] else None,
        "max_uses": r["max_uses"],
        "uses_count": r["uses_count"],
        "start_date": r["start_date"].isoformat(),
        "end_date": r["end_date"].isoformat() if r["end_date"] else None,
        "is_active": r["is_active"],
        "notes": r["notes"],
    }
