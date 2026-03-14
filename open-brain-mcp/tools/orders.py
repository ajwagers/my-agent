"""Summit Pine order fulfillment pipeline."""
import json
from datetime import date, timedelta

from db import get_pool


async def create_order(
    order_number: str, customer_name: str = None, customer_email: str = None,
    channel: str = "shopify", items: list = None, subtotal: float = None,
    shipping: float = 0, tax: float = None, shipping_address: dict = None,
    is_subscription: bool = False, subscription_interval_days: int = None,
    notes: str = None,
) -> dict:
    pool = await get_pool()
    total = (subtotal or 0) + (shipping or 0) + (tax or 0)
    guarantee_exp = (date.today() + timedelta(days=60)).isoformat() if not is_subscription else None
    # Compute tax if not provided (7% Orlando)
    if subtotal and tax is None:
        tax = round(subtotal * 0.07, 2)
        total = subtotal + (shipping or 0) + tax

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO orders
               (order_number, customer_name, customer_email, channel, status,
                items, subtotal, shipping, tax, total, shipping_address,
                is_subscription, subscription_interval_days, notes, guarantee_expires_at)
               VALUES ($1,$2,$3,$4,'pending',$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
               RETURNING id, order_number, status, created_at""",
            order_number, customer_name, customer_email, channel,
            json.dumps(items or []), subtotal, shipping, tax, total,
            json.dumps(shipping_address) if shipping_address else None,
            is_subscription, subscription_interval_days, notes,
            date.fromisoformat(guarantee_exp) if guarantee_exp else None,
        )
    return {
        "id": str(row["id"]),
        "order_number": row["order_number"],
        "status": row["status"],
        "total": total,
        "created_at": row["created_at"].isoformat(),
        "guarantee_expires_at": guarantee_exp,
    }


async def update_order_status(
    order_number: str, status: str,
    tracking_number: str = None, notes: str = None,
) -> dict:
    pool = await get_pool()
    sets = ["status=$1", "updated_at=NOW()"]
    args = [status]
    if tracking_number:
        args.append(tracking_number)
        sets.append(f"tracking_number=${len(args)}")
    if notes:
        args.append(notes)
        sets.append(f"notes=${len(args)}")
    args.append(order_number)
    sql = f"UPDATE orders SET {', '.join(sets)} WHERE order_number=${len(args)} RETURNING order_number, status"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": f"Order {order_number!r} not found"}
    return {"order_number": row["order_number"], "status": row["status"], "updated": True}


async def get_order(order_number: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE order_number=$1", order_number)
    if not row:
        return {"error": f"Order {order_number!r} not found"}
    return _order_row(row)


async def list_orders(status: str = None, channel: str = None, limit: int = 50) -> list[dict]:
    pool = await get_pool()
    conditions, args = [], []
    if status:
        args.append(status)
        conditions.append(f"status=${len(args)}")
    if channel:
        args.append(channel)
        conditions.append(f"channel=${len(args)}")
    args.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM orders {where} ORDER BY created_at DESC LIMIT ${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_order_row(r) for r in rows]


async def list_orders_by_status(status: str) -> list[dict]:
    return await list_orders(status=status)


def _order_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "order_number": r["order_number"],
        "customer_name": r["customer_name"],
        "customer_email": r["customer_email"],
        "channel": r["channel"],
        "status": r["status"],
        "items": r["items"],
        "subtotal": float(r["subtotal"]) if r["subtotal"] else None,
        "shipping": float(r["shipping"]) if r["shipping"] else None,
        "tax": float(r["tax"]) if r["tax"] else None,
        "total": float(r["total"]) if r["total"] else None,
        "tracking_number": r["tracking_number"],
        "is_subscription": r["is_subscription"],
        "notes": r["notes"],
        "guarantee_expires_at": r["guarantee_expires_at"].isoformat() if r["guarantee_expires_at"] else None,
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }
