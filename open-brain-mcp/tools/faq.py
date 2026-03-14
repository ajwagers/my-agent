"""Summit Pine FAQ / customer support knowledge base."""
import json

from db import get_pool
from embeddings import embed, vec_to_str


async def search_faq(query: str, limit: int = 5, threshold: float = 0.5,
                     category: str = None) -> list[dict]:
    pool = await get_pool()
    emb = await embed(query)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM search_faq($1::vector, $2, $3, $4)",
            vec_to_str(emb), threshold, limit, category,
        )
        # Increment usage_count for matched entries
        if rows:
            ids = [str(r["id"]) for r in rows]
            await conn.execute(
                "UPDATE faq_entries SET usage_count = usage_count + 1 WHERE id = ANY($1::uuid[])",
                ids,
            )
    return [
        {
            "id": str(r["id"]),
            "question": r["question"],
            "answer": r["answer"],
            "category": r["category"],
            "guardrail": r["guardrail"],
            "similarity": round(float(r["similarity"]), 4),
        }
        for r in rows
    ]


async def add_faq_entry(question: str, answer: str, category: str,
                         guardrail: str = None) -> dict:
    pool = await get_pool()
    emb = await embed(f"{question} {answer}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO faq_entries (question, answer, category, embedding, guardrail)
               VALUES ($1, $2, $3, $4::vector, $5)
               RETURNING id, category""",
            question, answer, category, vec_to_str(emb), guardrail,
        )
    return {"id": str(row["id"]), "category": row["category"], "created": True}


async def update_faq_entry(faq_id: str, question: str = None,
                            answer: str = None, category: str = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT question, answer FROM faq_entries WHERE id=$1", faq_id
        )
        if not row:
            return {"error": "FAQ entry not found"}
        new_q = question or row["question"]
        new_a = answer or row["answer"]
        emb = await embed(f"{new_q} {new_a}")
        sets = ["question=$1", "answer=$2", "embedding=$3::vector", "updated_at=NOW()"]
        args = [new_q, new_a, vec_to_str(emb)]
        if category:
            args.append(category)
            sets.append(f"category=${len(args)}")
        args.append(faq_id)
        sql = f"UPDATE faq_entries SET {', '.join(sets)} WHERE id=${len(args)}"
        await conn.execute(sql, *args)
    return {"id": faq_id, "updated": True}


async def list_faq_by_category(category: str = None) -> list[dict]:
    pool = await get_pool()
    if category:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, question, answer, category, guardrail, usage_count "
                "FROM faq_entries WHERE category=$1 ORDER BY usage_count DESC",
                category,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, question, answer, category, guardrail, usage_count "
                "FROM faq_entries ORDER BY category, usage_count DESC"
            )
    return [
        {"id": str(r["id"]), "question": r["question"], "answer": r["answer"],
         "category": r["category"], "guardrail": r["guardrail"],
         "usage_count": r["usage_count"]}
        for r in rows
    ]
