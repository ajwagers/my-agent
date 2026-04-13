"""Direct psycopg2 connection pool for the Summit Pine Streamlit UI.

Connects using SUMMIT_PINE_DB_URL (which uses the sp_app restricted role by default,
or brain credentials if SP_APP credentials are not configured).
"""
import os
import psycopg2
from psycopg2 import pool as pg_pool
import streamlit as st

_DB_URL = os.getenv(
    "SUMMIT_PINE_DB_URL",
    "postgresql://brain:brain@postgres-brain:5432/brain",
)

@st.cache_resource
def get_pool():
    return pg_pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=_DB_URL)


def query(sql: str, params=None) -> list[dict]:
    """Execute a SELECT and return list of dicts."""
    p = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        p.putconn(conn)


def execute(sql: str, params=None) -> None:
    """Execute an INSERT/UPDATE/DELETE."""
    p = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


def scalar(sql: str, params=None):
    """Return a single scalar value."""
    rows = query(sql, params)
    if rows:
        return list(rows[0].values())[0]
    return None
