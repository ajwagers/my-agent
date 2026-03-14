"""
Open Brain MCP — FastAPI server exposing:
  • REST API  (http://open-brain-mcp:8002/tools/*)   ← used by agent-core skills
  • MCP SSE   (http://open-brain-mcp:8002/mcp/sse)   ← used by Claude Desktop / Cursor
"""
import hashlib
import json
import os
import asyncio

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mcp.server import Server as MCPServer
from mcp.server.sse import SseServerTransport
import mcp.types as mcp_types

import db
from embeddings import embed, vec_to_str
import tools.thoughts as thoughts
import tools.calendar as calendar_tools
import tools.household as household
import tools.inventory as inventory
import tools.orders as orders
import tools.faq as faq

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Open Brain MCP", version="1.0.0")
mcp_server = MCPServer("open-brain")
sse = SseServerTransport("/mcp/messages")


# ── Startup: init DB schema, seed data, embed pre-seeded rows ─────────────────

@app.on_event("startup")
async def startup():
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Load and run init.sql
        sql_dir = os.path.join(os.path.dirname(__file__), "sql")
        with open(os.path.join(sql_dir, "init.sql")) as f:
            await conn.execute(f.read())
        # Seed data (idempotent — uses ON CONFLICT DO NOTHING)
        with open(os.path.join(sql_dir, "seed.sql")) as f:
            await conn.execute(f.read())
    # Backfill missing embeddings in a background task
    asyncio.create_task(_backfill_embeddings())
    # Ingest identity files from /agent/ into thoughts table
    asyncio.create_task(_ingest_identity_files())


@app.on_event("shutdown")
async def shutdown():
    await db.close_pool()


async def _backfill_embeddings():
    """Generate embeddings for any seeded rows that lack them."""
    pool = await db.get_pool()
    try:
        async with pool.acquire() as conn:
            # FAQ entries
            rows = await conn.fetch("SELECT id, question, answer FROM faq_entries WHERE embedding IS NULL")
            for r in rows:
                emb = await embed(f"{r['question']} {r['answer']}")
                await conn.execute(
                    "UPDATE faq_entries SET embedding=$1::vector WHERE id=$2",
                    vec_to_str(emb), r["id"],
                )
            # Inventory items
            rows = await conn.fetch("SELECT id, name, category, notes FROM inventory_items WHERE embedding IS NULL")
            for r in rows:
                emb = await embed(f"{r['category']} {r['name']}: {r['notes'] or ''}")
                await conn.execute(
                    "UPDATE inventory_items SET embedding=$1::vector WHERE id=$2",
                    vec_to_str(emb), r["id"],
                )
    except Exception as e:
        print(f"[backfill] error: {e}", flush=True)


# ── Identity file ingest ──────────────────────────────────────────────────────

IDENTITY_DIR = os.getenv("IDENTITY_DIR", "/agent")

# Maps filename → metadata type tag stored with the thought
_IDENTITY_FILES = {
    "SOUL.md":     "agent_soul",       # Mr. Bultitude's character, voice, personality
    "USER.md":     "owner_profile",    # Andy's background, preferences, goals
    "IDENTITY.md": "agent_identity",   # Agent name, nature, vibe
    "AGENTS.md":   "agent_directives", # Behavioural rules and operational guidance
}


async def _ingest_identity_files():
    """Read identity .md files from IDENTITY_DIR and store in the thoughts table.

    Idempotent: each file is keyed by filename in metadata. If the MD5 hash of
    the content matches what is already stored, the row is left untouched. When
    the file changes (e.g. you edit USER.md), the row is updated in place and
    the embedding is regenerated. Called as a background task at startup.
    """
    # Wait briefly so init.sql has committed before we write
    await asyncio.sleep(3)
    pool = await db.get_pool()

    for filename, doc_type in _IDENTITY_FILES.items():
        path = os.path.join(IDENTITY_DIR, filename)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                continue

            file_hash = hashlib.md5(content.encode()).hexdigest()

            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    """SELECT id, metadata->>'hash' AS hash
                       FROM thoughts
                       WHERE source = 'identity_file'
                         AND metadata->>'file' = $1
                       LIMIT 1""",
                    filename,
                )

                if existing and existing["hash"] == file_hash:
                    continue  # unchanged — nothing to do

                emb = await embed(content)
                meta = json.dumps({"file": filename, "type": doc_type, "hash": file_hash})

                if existing:
                    await conn.execute(
                        """UPDATE thoughts
                           SET content=$1, embedding=$2::vector,
                               metadata=metadata || $3::jsonb,
                               updated_at=NOW()
                           WHERE id=$4""",
                        content, vec_to_str(emb), meta, existing["id"],
                    )
                    print(f"[identity] updated {filename} (content changed)", flush=True)
                else:
                    await conn.execute(
                        """INSERT INTO thoughts (content, embedding, metadata, source)
                           VALUES ($1, $2::vector, $3::jsonb, 'identity_file')""",
                        content, vec_to_str(emb), meta,
                    )
                    print(f"[identity] ingested {filename}", flush=True)

        except Exception as e:
            print(f"[identity] failed to ingest {filename}: {e}", flush=True)


# ── Health ────────────────────────────────────────────────────────────────────

@app.post("/tools/reingest_identity")
async def reingest_identity():
    """Force re-ingest of all identity files, ignoring cached hashes.

    Useful after editing SOUL.md, USER.md, etc. without restarting the container.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM thoughts WHERE source = 'identity_file'"
        )
    await _ingest_identity_files()
    return {"status": "ok", "files": list(_IDENTITY_FILES.keys())}


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ── REST API — Thoughts ───────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
    content: str
    source: str = "telegram"

class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    threshold: float = 0.5

class ListThoughtsRequest(BaseModel):
    limit: int = 20
    thought_type: str = None
    topic: str = None
    person: str = None
    days: int = None


@app.post("/tools/capture_thought")
async def api_capture_thought(req: CaptureRequest):
    return await thoughts.capture_thought(req.content, req.source)

@app.post("/tools/search_thoughts")
async def api_search_thoughts(req: SearchRequest):
    return await thoughts.search_thoughts(req.query, req.limit, req.threshold)

@app.post("/tools/list_thoughts")
async def api_list_thoughts(req: ListThoughtsRequest):
    return await thoughts.list_thoughts(req.limit, req.thought_type, req.topic, req.person, req.days)

@app.get("/tools/thought_stats")
async def api_thought_stats():
    return await thoughts.thought_stats()


# ── REST API — Calendar ───────────────────────────────────────────────────────

class AddMemberRequest(BaseModel):
    name: str
    role: str = None

class AddEventRequest(BaseModel):
    title: str
    start_time: str
    event_type: str = "family"
    description: str = None
    end_time: str = None
    all_day: bool = False
    family_member_id: str = None
    recurrence_rule: str = None
    location: str = None
    metadata: dict = None
    sync_outlook: bool = True

class UpdateEventRequest(BaseModel):
    title: str = None
    description: str = None
    start_time: str = None
    end_time: str = None
    location: str = None
    event_type: str = None

class WeekScheduleRequest(BaseModel):
    date: str = None
    family_member_id: str = None

class SearchEventsRequest(BaseModel):
    query: str
    event_type: str = None
    days_ahead: int = 30

class UpcomingDatesRequest(BaseModel):
    days_ahead: int = 30


@app.post("/tools/add_family_member")
async def api_add_family_member(req: AddMemberRequest):
    return await calendar_tools.add_family_member(req.name, req.role)

@app.get("/tools/list_family_members")
async def api_list_family_members():
    return await calendar_tools.list_family_members()

@app.post("/tools/add_calendar_event")
async def api_add_calendar_event(req: AddEventRequest):
    return await calendar_tools.add_calendar_event(**req.model_dump())

@app.put("/tools/update_calendar_event/{event_id}")
async def api_update_calendar_event(event_id: str, req: UpdateEventRequest):
    return await calendar_tools.update_calendar_event(event_id, **req.model_dump(exclude_none=True))

@app.delete("/tools/delete_calendar_event/{event_id}")
async def api_delete_calendar_event(event_id: str):
    return await calendar_tools.delete_calendar_event(event_id)

@app.post("/tools/get_week_schedule")
async def api_get_week_schedule(req: WeekScheduleRequest):
    return await calendar_tools.get_week_schedule(req.date, req.family_member_id)

@app.post("/tools/search_events")
async def api_search_events(req: SearchEventsRequest):
    return await calendar_tools.search_events(req.query, req.event_type, req.days_ahead)

@app.post("/tools/get_upcoming_dates")
async def api_get_upcoming_dates(req: UpcomingDatesRequest):
    return await calendar_tools.get_upcoming_dates(req.days_ahead)


# ── REST API — Household KB ───────────────────────────────────────────────────

class AddFactRequest(BaseModel):
    category: str
    key: str
    value: str
    notes: str = None

class SearchFactsRequest(BaseModel):
    query: str
    category: str = None
    limit: int = 5
    threshold: float = 0.5

class UpdateFactRequest(BaseModel):
    value: str = None
    notes: str = None


@app.post("/tools/add_household_fact")
async def api_add_household_fact(req: AddFactRequest):
    return await household.add_household_fact(req.category, req.key, req.value, req.notes)

@app.post("/tools/search_household_facts")
async def api_search_household_facts(req: SearchFactsRequest):
    return await household.search_household_facts(req.query, req.category, req.limit, req.threshold)

@app.put("/tools/update_household_fact/{fact_id}")
async def api_update_household_fact(fact_id: str, req: UpdateFactRequest):
    return await household.update_household_fact(fact_id, req.value, req.notes)

@app.get("/tools/list_household_facts")
async def api_list_household_facts(category: str = None):
    return await household.list_household_facts(category)


# ── REST API — Inventory ──────────────────────────────────────────────────────

class AddItemRequest(BaseModel):
    sku: str; name: str; category: str; unit: str
    quantity_on_hand: float = 0; reorder_threshold: float = None
    reorder_quantity: float = None; unit_cost: float = None
    supplier: str = None; supplier_lead_days: int = None
    is_critical: bool = False; notes: str = None

class UpdateInventoryRequest(BaseModel):
    quantity_on_hand: float = None; unit_cost: float = None
    notes: str = None; reorder_threshold: float = None

class RecordBatchRequest(BaseModel):
    batch_number: str; product_type: str; batch_date: str
    quantity_produced: int; ingredients_used: list = None; qc_notes: str = None

class UpdateBatchRequest(BaseModel):
    status: str; ph_test_result: float = None; qc_notes: str = None


@app.post("/tools/add_inventory_item")
async def api_add_inventory_item(req: AddItemRequest):
    return await inventory.add_inventory_item(**req.model_dump())

@app.put("/tools/update_inventory/{sku}")
async def api_update_inventory(sku: str, req: UpdateInventoryRequest):
    return await inventory.update_inventory(sku, **req.model_dump(exclude_none=True))

@app.get("/tools/get_inventory_item/{sku}")
async def api_get_inventory_item(sku: str):
    return await inventory.get_inventory_item(sku)

@app.get("/tools/list_inventory")
async def api_list_inventory(category: str = None):
    return await inventory.list_inventory(category)

@app.get("/tools/list_low_stock")
async def api_list_low_stock():
    return await inventory.list_low_stock()

@app.post("/tools/record_production_batch")
async def api_record_production_batch(req: RecordBatchRequest):
    return await inventory.record_production_batch(**req.model_dump())

@app.put("/tools/update_batch_status/{batch_number}")
async def api_update_batch_status(batch_number: str, req: UpdateBatchRequest):
    return await inventory.update_batch_status(batch_number, **req.model_dump(exclude_none=True))

@app.get("/tools/list_batches")
async def api_list_batches(status: str = None):
    return await inventory.list_batches(status)

@app.get("/tools/get_batch_status/{batch_number}")
async def api_get_batch_status(batch_number: str):
    return await inventory.get_batch_status(batch_number)


# ── REST API — Orders ─────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    order_number: str; customer_name: str = None; customer_email: str = None
    channel: str = "shopify"; items: list = None; subtotal: float = None
    shipping: float = 0; tax: float = None; shipping_address: dict = None
    is_subscription: bool = False; subscription_interval_days: int = None
    notes: str = None

class UpdateOrderRequest(BaseModel):
    status: str; tracking_number: str = None; notes: str = None


@app.post("/tools/create_order")
async def api_create_order(req: CreateOrderRequest):
    return await orders.create_order(**req.model_dump())

@app.put("/tools/update_order_status/{order_number}")
async def api_update_order_status(order_number: str, req: UpdateOrderRequest):
    return await orders.update_order_status(order_number, **req.model_dump(exclude_none=True))

@app.get("/tools/get_order/{order_number}")
async def api_get_order(order_number: str):
    return await orders.get_order(order_number)

@app.get("/tools/list_orders")
async def api_list_orders(status: str = None, channel: str = None, limit: int = 50):
    return await orders.list_orders(status, channel, limit)


# ── REST API — FAQ ────────────────────────────────────────────────────────────

class SearchFAQRequest(BaseModel):
    query: str; limit: int = 5; threshold: float = 0.5; category: str = None

class AddFAQRequest(BaseModel):
    question: str; answer: str; category: str; guardrail: str = None

class UpdateFAQRequest(BaseModel):
    question: str = None; answer: str = None; category: str = None


@app.post("/tools/search_faq")
async def api_search_faq(req: SearchFAQRequest):
    return await faq.search_faq(req.query, req.limit, req.threshold, req.category)

@app.post("/tools/add_faq_entry")
async def api_add_faq_entry(req: AddFAQRequest):
    return await faq.add_faq_entry(req.question, req.answer, req.category, req.guardrail)

@app.put("/tools/update_faq_entry/{faq_id}")
async def api_update_faq_entry(faq_id: str, req: UpdateFAQRequest):
    return await faq.update_faq_entry(faq_id, req.question, req.answer, req.category)

@app.get("/tools/list_faq_by_category")
async def api_list_faq_by_category(category: str = None):
    return await faq.list_faq_by_category(category)


# ── MCP Server — tool registry ────────────────────────────────────────────────

_MCP_TOOLS = [
    # Core thoughts
    mcp_types.Tool(name="capture_thought", description="Save a thought, note, or memory to the brain database.",
        inputSchema={"type":"object","properties":{"content":{"type":"string"},"source":{"type":"string","default":"mcp"}},"required":["content"]}),
    mcp_types.Tool(name="search_thoughts", description="Semantic search over stored thoughts.",
        inputSchema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":10},"threshold":{"type":"number","default":0.5}},"required":["query"]}),
    mcp_types.Tool(name="list_thoughts", description="List recent thoughts with optional filters.",
        inputSchema={"type":"object","properties":{"limit":{"type":"integer","default":20},"thought_type":{"type":"string"},"topic":{"type":"string"},"days":{"type":"integer"}}}),
    mcp_types.Tool(name="thought_stats", description="Statistics about stored thoughts.",
        inputSchema={"type":"object","properties":{}}),
    # Calendar
    mcp_types.Tool(name="add_calendar_event", description="Add a calendar event (syncs to Outlook/Skylight).",
        inputSchema={"type":"object","properties":{"title":{"type":"string"},"start_time":{"type":"string"},"event_type":{"type":"string","default":"family"},"description":{"type":"string"},"end_time":{"type":"string"},"all_day":{"type":"boolean"},"location":{"type":"string"},"sync_outlook":{"type":"boolean","default":True}},"required":["title","start_time"]}),
    mcp_types.Tool(name="get_week_schedule", description="Get calendar events for a week.",
        inputSchema={"type":"object","properties":{"date":{"type":"string"}}}),
    mcp_types.Tool(name="get_upcoming_dates", description="List upcoming important dates.",
        inputSchema={"type":"object","properties":{"days_ahead":{"type":"integer","default":30}}}),
    # Household
    mcp_types.Tool(name="search_household_facts", description="Search household knowledge base.",
        inputSchema={"type":"object","properties":{"query":{"type":"string"},"category":{"type":"string"}},"required":["query"]}),
    mcp_types.Tool(name="add_household_fact", description="Add a fact to the household knowledge base.",
        inputSchema={"type":"object","properties":{"category":{"type":"string"},"key":{"type":"string"},"value":{"type":"string"},"notes":{"type":"string"}},"required":["category","key","value"]}),
    # Inventory
    mcp_types.Tool(name="list_low_stock", description="List Summit Pine inventory items below reorder threshold.",
        inputSchema={"type":"object","properties":{}}),
    mcp_types.Tool(name="list_inventory", description="List all inventory items.",
        inputSchema={"type":"object","properties":{"category":{"type":"string"}}}),
    mcp_types.Tool(name="update_inventory", description="Update inventory quantity or cost for a SKU.",
        inputSchema={"type":"object","properties":{"sku":{"type":"string"},"quantity_on_hand":{"type":"number"},"unit_cost":{"type":"number"},"notes":{"type":"string"}},"required":["sku"]}),
    mcp_types.Tool(name="record_production_batch", description="Record a Summit Pine production batch.",
        inputSchema={"type":"object","properties":{"batch_number":{"type":"string"},"product_type":{"type":"string"},"batch_date":{"type":"string"},"quantity_produced":{"type":"integer"},"qc_notes":{"type":"string"}},"required":["batch_number","product_type","batch_date","quantity_produced"]}),
    # Orders
    mcp_types.Tool(name="list_orders", description="List Summit Pine orders.",
        inputSchema={"type":"object","properties":{"status":{"type":"string"},"channel":{"type":"string"},"limit":{"type":"integer","default":50}}}),
    mcp_types.Tool(name="update_order_status", description="Update order status and tracking.",
        inputSchema={"type":"object","properties":{"order_number":{"type":"string"},"status":{"type":"string"},"tracking_number":{"type":"string"}},"required":["order_number","status"]}),
    # FAQ
    mcp_types.Tool(name="search_faq", description="Search Summit Pine FAQ for customer support.",
        inputSchema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":5}},"required":["query"]}),
]


@mcp_server.list_tools()
async def list_mcp_tools():
    return _MCP_TOOLS


@mcp_server.call_tool()
async def call_mcp_tool(name: str, arguments: dict | None):
    args = arguments or {}
    try:
        # Route to the appropriate tool function
        if name == "capture_thought":
            result = await thoughts.capture_thought(args["content"], args.get("source", "mcp"))
        elif name == "search_thoughts":
            result = await thoughts.search_thoughts(args["query"], args.get("limit", 10), args.get("threshold", 0.5))
        elif name == "list_thoughts":
            result = await thoughts.list_thoughts(**args)
        elif name == "thought_stats":
            result = await thoughts.thought_stats()
        elif name == "add_calendar_event":
            result = await calendar_tools.add_calendar_event(**args)
        elif name == "get_week_schedule":
            result = await calendar_tools.get_week_schedule(args.get("date"))
        elif name == "get_upcoming_dates":
            result = await calendar_tools.get_upcoming_dates(args.get("days_ahead", 30))
        elif name == "search_household_facts":
            result = await household.search_household_facts(args["query"], args.get("category"))
        elif name == "add_household_fact":
            result = await household.add_household_fact(args["category"], args["key"], args["value"], args.get("notes"))
        elif name == "list_low_stock":
            result = await inventory.list_low_stock()
        elif name == "list_inventory":
            result = await inventory.list_inventory(args.get("category"))
        elif name == "update_inventory":
            result = await inventory.update_inventory(args["sku"], **{k: v for k, v in args.items() if k != "sku"})
        elif name == "record_production_batch":
            result = await inventory.record_production_batch(**args)
        elif name == "list_orders":
            result = await orders.list_orders(**args)
        elif name == "update_order_status":
            result = await orders.update_order_status(args["order_number"], args["status"], args.get("tracking_number"))
        elif name == "search_faq":
            result = await faq.search_faq(args["query"], args.get("limit", 5))
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}
    return [mcp_types.TextContent(type="text", text=json.dumps(result))]


# ── MCP SSE transport ─────────────────────────────────────────────────────────

@app.get("/mcp/sse")
async def mcp_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())


@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
