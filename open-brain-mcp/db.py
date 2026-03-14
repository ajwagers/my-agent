"""asyncpg connection pool for postgres-brain."""
import asyncpg
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://brain:brain@postgres-brain:5432/brain",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
