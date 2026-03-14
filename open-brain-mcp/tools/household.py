"""Household knowledge base — home facts, appliances, wifi, utilities, etc."""
import json

from db import get_pool
from embeddings import embed, vec_to_str


async def add_household_fact(category: str, key: str, value: str, notes: str = None) -> dict:
    pool = await get_pool()
    emb = await embed(f"{category} {key}: {value}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO household_knowledge (category, key, value, embedding, notes)
               VALUES ($1, $2, $3, $4::vector, $5)
               RETURNING id, category, key""",
            category, key, value, vec_to_str(emb), notes,
        )
    return {"id": str(row["id"]), "category": row["category"], "key": row["key"]}


async def search_household_facts(query: str, category: str = None,
                                  limit: int = 5, threshold: float = 0.5) -> list[dict]:
    pool = await get_pool()
    emb = await embed(query)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM search_household_knowledge($1::vector, $2, $3, $4)",
            vec_to_str(emb), threshold, limit, category,
        )
    return [
        {
            "id": str(r["id"]),
            "category": r["category"],
            "key": r["key"],
            "value": r["value"],
            "notes": r["notes"],
            "similarity": round(float(r["similarity"]), 4),
        }
        for r in rows
    ]


async def update_household_fact(fact_id: str, value: str = None, notes: str = None) -> dict:
    pool = await get_pool()
    if not value and not notes:
        return {"error": "Provide value or notes to update"}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT category, key FROM household_knowledge WHERE id=$1", fact_id
        )
        if not row:
            return {"error": "Fact not found"}
        if value:
            new_emb = await embed(f"{row['category']} {row['key']}: {value}")
            await conn.execute(
                "UPDATE household_knowledge SET value=$1, embedding=$2::vector, updated_at=NOW() WHERE id=$3",
                value, vec_to_str(new_emb), fact_id,
            )
        if notes:
            await conn.execute(
                "UPDATE household_knowledge SET notes=$1, updated_at=NOW() WHERE id=$2",
                notes, fact_id,
            )
    return {"id": fact_id, "updated": True}


async def list_household_facts(category: str = None) -> list[dict]:
    pool = await get_pool()
    if category:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, category, key, value, notes FROM household_knowledge WHERE category=$1 ORDER BY key",
                category,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, category, key, value, notes FROM household_knowledge ORDER BY category, key"
            )
    return [
        {"id": str(r["id"]), "category": r["category"],
         "key": r["key"], "value": r["value"], "notes": r["notes"]}
        for r in rows
    ]
