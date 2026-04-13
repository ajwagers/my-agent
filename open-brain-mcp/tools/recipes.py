"""Summit Pine production recipe CRUD."""
import json
from typing import Optional

from db import get_pool


async def add_recipe(
    name: str,
    ingredients: list = None,
    instructions: str = None,
    servings: int = None,
    prep_time_minutes: int = None,
    tags: list = None,
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO recipes (name, ingredients, instructions, servings, prep_time_minutes, tags)
               VALUES ($1,$2,$3,$4,$5,$6)
               RETURNING id, name""",
            name, json.dumps(ingredients or []), instructions, servings, prep_time_minutes,
            tags or [],
        )
    return {"id": str(row["id"]), "name": row["name"], "created": True}


async def get_recipe(recipe_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM recipes WHERE id=$1", recipe_id)
    if not row:
        return {"error": f"Recipe {recipe_id!r} not found"}
    return _row(row)


async def list_recipes(tag: str = None) -> list[dict]:
    pool = await get_pool()
    if tag:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, servings, prep_time_minutes, tags FROM recipes WHERE $1 = ANY(tags) ORDER BY name",
                tag,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, servings, prep_time_minutes, tags FROM recipes ORDER BY name"
            )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "servings": r["servings"],
            "prep_time_minutes": r["prep_time_minutes"],
            "tags": list(r["tags"]) if r["tags"] else [],
        }
        for r in rows
    ]


async def update_recipe(
    recipe_id: str,
    name: str = None,
    ingredients: list = None,
    instructions: str = None,
    servings: int = None,
    prep_time_minutes: int = None,
    tags: list = None,
) -> dict:
    pool = await get_pool()
    sets, args = [], []
    if name is not None:
        args.append(name); sets.append(f"name=${len(args)}")
    if ingredients is not None:
        args.append(json.dumps(ingredients)); sets.append(f"ingredients=${len(args)}")
    if instructions is not None:
        args.append(instructions); sets.append(f"instructions=${len(args)}")
    if servings is not None:
        args.append(servings); sets.append(f"servings=${len(args)}")
    if prep_time_minutes is not None:
        args.append(prep_time_minutes); sets.append(f"prep_time_minutes=${len(args)}")
    if tags is not None:
        args.append(tags); sets.append(f"tags=${len(args)}")
    if not sets:
        return {"error": "Nothing to update"}
    args.append(recipe_id)
    sql = f"UPDATE recipes SET {', '.join(sets)} WHERE id=${len(args)} RETURNING id, name"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if not row:
        return {"error": f"Recipe {recipe_id!r} not found"}
    return {"id": str(row["id"]), "name": row["name"], "updated": True}


async def delete_recipe(recipe_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM recipes WHERE id=$1", recipe_id)
    return {"deleted": result == "DELETE 1"}


def _row(r) -> dict:
    ings = r["ingredients"]
    if isinstance(ings, str):
        try:
            ings = json.loads(ings)
        except Exception:
            ings = []
    elif ings is None:
        ings = []
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "ingredients": ings,
        "instructions": r["instructions"],
        "servings": r["servings"],
        "prep_time_minutes": r["prep_time_minutes"],
        "tags": list(r["tags"]) if r["tags"] else [],
    }
