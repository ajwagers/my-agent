"""Personal to-do / shopping list CRUD for the Open Brain MCP."""

from db import get_pool


async def add_todo(
    text: str,
    category: str = "task",
    priority: str = "medium",
    source: str = "telegram",
    user_id: str = "owner",
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO todos (user_id, text, category, priority, source)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id, created_at""",
            user_id, text, category, priority, source,
        )
    return {
        "id": row["id"],
        "text": text,
        "category": category,
        "priority": priority,
        "added": True,
    }


async def list_todos(
    status: str = "pending",
    category: str = None,
    user_id: str = "owner",
) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if category:
            rows = await conn.fetch(
                """SELECT id, text, category, priority, status, created_at
                   FROM todos
                   WHERE user_id=$1 AND status=$2 AND category=$3
                   ORDER BY
                     CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     created_at""",
                user_id, status, category,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, text, category, priority, status, created_at
                   FROM todos
                   WHERE user_id=$1 AND status=$2
                   ORDER BY
                     CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     created_at""",
                user_id, status,
            )
    return [
        {
            "id": r["id"],
            "text": r["text"],
            "category": r["category"],
            "priority": r["priority"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def complete_todo(todo_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET status='done', completed_at=NOW() WHERE id=$1 RETURNING text",
            todo_id,
        )
    if row:
        return {"id": todo_id, "text": row["text"], "status": "done"}
    return {"error": f"Todo #{todo_id} not found"}


async def delete_todo(todo_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM todos WHERE id=$1 RETURNING text",
            todo_id,
        )
    if row:
        return {"deleted": todo_id, "text": row["text"]}
    return {"error": f"Todo #{todo_id} not found"}
