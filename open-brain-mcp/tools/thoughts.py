"""Core OB1 thought capture and retrieval."""
import json
from datetime import datetime, timezone, timedelta

from db import get_pool
from embeddings import embed, vec_to_str
from metadata import extract_metadata


async def capture_thought(content: str, source: str = "telegram") -> dict:
    pool = await get_pool()
    emb, meta = await _embed_and_extract(content)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO thoughts (content, embedding, metadata, source)
               VALUES ($1, $2::vector, $3, $4)
               RETURNING id, created_at""",
            content, vec_to_str(emb), json.dumps(meta), source,
        )
    return {
        "id": str(row["id"]),
        "content": content,
        "metadata": meta,
        "created_at": row["created_at"].isoformat(),
    }


async def search_thoughts(query: str, limit: int = 10, threshold: float = 0.5) -> list[dict]:
    pool = await get_pool()
    emb = await embed(query)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM search_thoughts($1::vector, $2, $3)""",
            vec_to_str(emb), threshold, limit,
        )
    return [
        {
            "id": str(r["id"]),
            "content": r["content"],
            "metadata": r["metadata"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
            "similarity": round(float(r["similarity"]), 4),
        }
        for r in rows
    ]


async def list_thoughts(limit: int = 20, thought_type: str = None,
                        topic: str = None, person: str = None,
                        days: int = None) -> list[dict]:
    pool = await get_pool()
    conditions = []
    args = []

    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        args.append(cutoff)
        conditions.append(f"created_at >= ${len(args)}")

    if thought_type:
        args.append(thought_type)
        conditions.append(f"metadata->>'type' = ${len(args)}")

    if topic:
        args.append(f'["{topic}"]')
        conditions.append(f"metadata->'topics' @> ${len(args)}::jsonb")

    if person:
        args.append(f'["{person}"]')
        conditions.append(f"metadata->'people' @> ${len(args)}::jsonb")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    args.append(limit)
    query = f"""
        SELECT id, content, metadata, source, created_at
        FROM thoughts {where}
        ORDER BY created_at DESC
        LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [
        {
            "id": str(r["id"]),
            "content": r["content"],
            "metadata": r["metadata"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def thought_stats() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM thoughts")
        types = await conn.fetch(
            "SELECT metadata->>'type' AS t, COUNT(*) AS n FROM thoughts GROUP BY t ORDER BY n DESC"
        )
        topics_raw = await conn.fetch(
            "SELECT jsonb_array_elements_text(metadata->'topics') AS topic, COUNT(*) AS n "
            "FROM thoughts WHERE metadata->'topics' IS NOT NULL GROUP BY topic ORDER BY n DESC LIMIT 10"
        )
        people_raw = await conn.fetch(
            "SELECT jsonb_array_elements_text(metadata->'people') AS person, COUNT(*) AS n "
            "FROM thoughts WHERE metadata->'people' IS NOT NULL GROUP BY person ORDER BY n DESC LIMIT 10"
        )
    return {
        "total": total,
        "by_type": {r["t"] or "unknown": r["n"] for r in types},
        "top_topics": [{"topic": r["topic"], "count": r["n"]} for r in topics_raw],
        "top_people": [{"person": r["person"], "count": r["n"]} for r in people_raw],
    }


async def _embed_and_extract(content: str):
    import asyncio
    emb, meta = await asyncio.gather(embed(content), extract_metadata(content))
    return emb, meta
