# Setup Guide 8 — Open Brain MCP, Personal Memory & Privacy Safeguards

> **Phase 6** — Covers: PostgreSQL + pgvector personal memory (Open Brain MCP), identity file auto-ingest, three-layer privacy safeguards, Mumble owner certificate authentication, Summit Pine business assistant skills, Telegram `/remember` command, and voice "save that" capture.
>
> **Prerequisites:** SETUP_GUIDE_7.md complete (Mumble voice gateway running), all Phase 4E services up (calendar, python_exec). Stack is running with `docker compose up -d`.

---

## What This Phase Adds

| Capability | Description |
|---|---|
| **Open Brain MCP** | Semantic personal memory backed by PostgreSQL + pgvector. Stores thoughts, household facts, notes. 768-dim nomic-embed-text embeddings. |
| **Identity file ingest** | SOUL.md, USER.md, IDENTITY.md, AGENTS.md automatically embedded into memory at startup. Agent knows itself from first boot. |
| **Memory skills** | `memory_capture` and `memory_search` skills — agent can store and retrieve memories during conversations. |
| **Privacy safeguards** | Three-layer system: skill gate + memory middleware + system prompt directive. Personal data never leaks to non-private channels. |
| **Mumble cert auth** | Cryptographic owner authentication via Mumble client certificate hash. Grants `mumble_owner` channel (full private access). |
| **Telegram `/remember`** | Slash command to capture facts directly to brain memory from Telegram. |
| **Voice "save that"** | Say "save that" or "remember that" after a bot response in Mumble to capture it to memory. |
| **Summit Pine skills** | `sp_inventory`, `sp_orders`, `sp_faq` business assistant skills. |

---

## Step 1 — New Environment Variables

Open `.env` and add the following (below your existing entries):

```bash
# Open Brain MCP (PostgreSQL + pgvector)
BRAIN_POSTGRES_PASSWORD=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))">

# Mumble owner identity
MUMBLE_OWNER_USERNAMES=Andy          # Your Mumble display name (fallback until cert hash)
MUMBLE_OWNER_CERT_HASH=              # Leave blank — you'll fill this in after Step 6

# Outlook / MS Graph calendar auth
MS_GRAPH_CLIENT_ID=<your Azure app client ID>    # Skip if not using Outlook calendar

# Proton CalDAV (skip if not using Proton Calendar)
# PROTON_CALDAV_URL=http://proton-bridge:1080/...
# PROTON_CALDAV_USER=you@proton.me
# PROTON_CALDAV_PASSWORD=<bridge-generated password>
```

**Generate the brain password:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Step 2 — Pull the Embedding Model

Open Brain MCP uses `nomic-embed-text` for 768-dim embeddings — the same model already used by agent-core. If you pulled it previously it is already cached in `ollama_data`.

```bash
docker exec ollama-runner ollama pull nomic-embed-text
```

---

## Step 3 — Bring Up the New Services

The `docker-compose.yml` now includes `postgres-brain` and `open-brain-mcp`. Bring everything up:

```bash
docker compose up -d --build
```

This will:
1. Start `postgres-brain` (PostgreSQL 16 + pgvector, volume `brain_data`)
2. Build and start `open-brain-mcp` (waits for postgres-brain and ollama-runner healthy)
3. Rebuild `agent-core` with the new `memory_middleware.py`, new skills, and privacy safeguards
4. Rebuild `mumble-bot` with certificate hash authentication and "save that" support

Check that everything is up:
```bash
docker compose ps
```

All services should show `running`. `open-brain-mcp` may take 30–60 seconds after `postgres-brain` is ready while it initialises the schema and starts embedding.

---

## Step 4 — Verify Open Brain MCP

### 4a. Check the logs

```bash
docker logs open-brain-mcp --tail 40
```

You should see:
```
INFO: Schema initialised (thoughts, household_facts, locations, notes)
INFO: Identity file ingest task started
INFO: Ingesting SOUL.md (agent_soul)...
INFO: Ingesting USER.md (owner_profile)...
INFO: Ingesting IDENTITY.md (agent_identity)...
INFO: Ingesting AGENTS.md (agent_directives)...
INFO: Identity ingest complete (4 files)
INFO: Application startup complete.
```

### 4b. Test the REST API

From the host (port 8002 is bound to `127.0.0.1`):

```bash
# Capture a thought
curl -s -X POST http://localhost:8002/tools/capture_thought \
  -H "Content-Type: application/json" \
  -d '{"content": "Test memory: the agent is working"}' | python3 -m json.tool

# Recall it back
curl -s -X POST http://localhost:8002/tools/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "is the agent working", "n": 3}' | python3 -m json.tool
```

You should get a response with `items` containing your test thought.

### 4c. Force re-ingest identity files (if needed)

If you updated an identity file and want to re-embed it without restarting:

```bash
curl -s -X POST http://localhost:8002/tools/reingest_identity | python3 -m json.tool
```

---

## Step 5 — Test the Telegram `/remember` Command

In Telegram, send:
```
/remember Andy prefers Earl Grey over any other tea
```

You should receive: `📝 Saving to memory...`

Then test recall through the agent:
```
Do you remember anything about my tea preferences?
```

The agent should recall the fact from Open Brain MCP memory.

---

## Step 6 — Mumble Owner Certificate Authentication

Without a certificate hash configured, the bot trusts whoever has the username in `MUMBLE_OWNER_USERNAMES`. This is fine for a private server but less secure — anyone who knows your Mumble display name could spoof it.

### 6a. Connect to Mumble with your client

Start your Mumble client and connect to your server (`localhost:64738` or your server's IP). Make sure you are using a **registered certificate** (Mumble generates one automatically on first use — just ensure it is saved).

### 6b. Check the bot logs for your cert hash

```bash
docker logs mumble-bot | grep "MUMBLE_OWNER_CERT_HASH"
```

You should see a line like:
```
INFO: Owner 'Andy' connected with cert hash abc123def456...
INFO: Set MUMBLE_OWNER_CERT_HASH=abc123def456... to use certificate-based auth
```

If you do not see this, make sure `MUMBLE_OWNER_USERNAMES` matches your Mumble display name exactly (case-sensitive).

### 6c. Copy the hash into `.env`

```bash
# In .env:
MUMBLE_OWNER_CERT_HASH=abc123def456...
```

### 6d. Restart mumble-bot

```bash
docker compose restart mumble-bot
```

### 6e. Verify

Connect to Mumble and ask: *"What channel are you using for me?"*

The bot should confirm it is using `mumble_owner` channel (or you can check logs: `docker logs mumble-bot | grep "channel="`).

---

## Step 7 — Test Privacy Safeguards

### 7a. Private channel test (Telegram)

In Telegram, ask:
```
What do you know about my calendar or personal preferences?
```

The agent should be able to access and share this information.

### 7b. Public channel test (web-ui at http://localhost:8501)

In the web UI, ask:
```
Tell me about the owner's preferences or calendar
```

The agent should refuse with something like:
> *"Personal details are only available on your private Telegram channel."*

This confirms Layer 3 (system prompt privacy directive) is working.

### 7c. Verify skill gate (Layer 1)

You can also test by looking at agent-core logs when a calendar request comes in from a non-private channel:
```bash
docker logs agent-core | grep "private"
```

---

## Step 8 — Voice "Save That" Command

With Mumble connected and the bot running:

1. Ask the agent something: *"What is the boiling point of water?"*
2. Wait for the response (bot will speak it and also display it in the Mumble text area)
3. Say: **"Save that"** or **"Remember that"** or **"Add that to memory"**
4. The bot will say: *"Saved."* and directly POST the last response to Open Brain MCP

To verify it was saved:
```bash
curl -s -X POST http://localhost:8002/tools/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "boiling point water", "n": 3}' | python3 -m json.tool
```

---

## Step 9 — Summit Pine Business Skills

If you are using Summit Pine business skills (`sp_inventory`, `sp_orders`, `sp_faq`), these are already registered in agent-core. Test them from Telegram:

```
Check inventory for item X
Look up order #12345
What are the most common customer questions?
```

Note: `sp_orders` is gated to private channels only — it will refuse on the web UI (customer data protection).

---

## Step 10 — Verify All Tests Still Pass

```bash
docker exec agent-core python -m pytest tests/ -q
```

Expected: **524 tests passing** (all prior tests plus new skill tests).

If any tests fail, check `docker logs agent-core` for import errors or missing env vars.

---

## Troubleshooting

### `open-brain-mcp` fails to start

**Symptom:** `Connection refused` in open-brain-mcp logs.

**Cause:** `postgres-brain` is not yet ready.

**Fix:** Wait 30 seconds and check again:
```bash
docker logs open-brain-mcp --tail 20
docker logs postgres-brain --tail 20
```

If postgres-brain is stuck, check `BRAIN_POSTGRES_PASSWORD` is set in `.env`.

---

### Identity files not being ingested

**Symptom:** Brain recalls nothing about the agent's personality.

**Check:**
```bash
docker logs open-brain-mcp | grep "Ingesting"
```

If no ingest lines appear, check that `agent-identity/` contains SOUL.md, USER.md, IDENTITY.md, AGENTS.md and that the volume mount is correct:
```bash
docker exec open-brain-mcp ls /agent/
```

Force re-ingest:
```bash
curl -s -X POST http://localhost:8002/tools/reingest_identity
```

---

### Mumble bot not recognising owner

**Symptom:** Bot responds with restricted information or says personal data is unavailable.

**Check cert hash discovery:**
```bash
docker logs mumble-bot | grep -i "cert\|owner\|hash"
```

Make sure `MUMBLE_OWNER_USERNAMES` exactly matches your Mumble display name (check Mumble client → Configure → Your Username).

---

### Brain context not appearing in agent responses

**Symptom:** Agent doesn't seem to recall memories even from Telegram.

**Check `BRAIN_URL` is set in agent-core:**
```bash
docker exec agent-core env | grep BRAIN
```

Should show: `BRAIN_URL=http://open-brain-mcp:8002`

**Check brain is reachable from agent-core:**
```bash
docker exec agent-core curl -s http://open-brain-mcp:8002/health
```

---

### Telegram flood control (`RetryAfter` errors)

**Symptom:** Bot stops responding; `telegram-gateway` logs show `telegram.error.RetryAfter: Flood control exceeded. Retry in XXXX seconds`.

**Cause:** Too many messages sent to the same chat in a short window (e.g. a burst of job completion notifications).

**Built-in protection (post-Phase-6 patch):**
- `_throttled_send()` in `bot.py` enforces a 1.1 s minimum between every outgoing Telegram message. This prevents a notification burst from triggering flood control.
- All send paths (startup greeting, ack, queue worker, notification subscriber) catch `RetryAfter` and log it instead of crashing. The bot stays alive during the lockout period.
- `JobManager.create()` deduplicates recurring jobs — asking the agent to schedule the same recurring job multiple times will not create duplicates.

**If flood control is already active:** It is a server-side countdown at Telegram — there is nothing to flush locally. The bot will resume sending automatically when the timer expires. Check remaining time:
```bash
docker logs telegram-gateway 2>&1 | grep "RetryAfter" | tail -3
```

**To check for duplicate jobs:**
```bash
REDIS_PASS=$(grep ^REDIS_PASSWORD .env | cut -d= -f2 | tr -d ' \r')
docker compose exec redis redis-cli -a "$REDIS_PASS" ZCOUNT jobs:scheduled -inf +inf
```
If count is unexpectedly high, clean all jobs atomically:
```bash
docker compose exec redis redis-cli -a "$REDIS_PASS" \
  EVAL "local k=redis.call('keys','jobs:*'); for _,v in ipairs(k) do redis.call('del',v) end; return #k" 0
```
Then re-schedule the desired job once from Telegram.

---

## Architecture Summary

```
Telegram /remember ─────────────────────────────────────────┐
Voice "save that" ──────────────────────────────────────────┤
                                                             ▼
                                               open-brain-mcp :8002
                                                (FastAPI + asyncpg)
                                                             │
                                               postgres-brain :5432
                                               (pgvector 768-dim)
                                                             │
agent-core /chat ──► build_brain_context() ──► /tools/recall │
                         │                                   │
                    channel filter                    /tools/capture_thought
                    (private vs public)                      ▲
                         │                                   │
                    inject into                   memory_capture skill
                    system prompt                 /remember Telegram cmd
                                                  "save that" voice cmd
```

**Channel trust model:**
- `telegram` + `cli` + `mumble_owner` → full memory access (personal + identity + household)
- `mumble` + `web-ui` + others → public access only (personal data filtered, skill gate blocks)

---

## What's Next

See the Phase 7 section below for Summit Pine Operations Expansion setup. After Phase 7, see `PRD.md` Phase 8 for the autonomy & integrations roadmap.

---

# Phase 7 — Summit Pine Operations Expansion

> **Phase 7** — Covers: labour hour tracking, recipe management, promotions/discount codes, receipt PDF ingestion, plain-text note ingestion (Quick Ingest), and a 10-tab Streamlit analytics dashboard.
>
> **Prerequisites:** Phase 6 complete (Open Brain MCP running, Summit Pine skills working, `sp_app` DB role in place). Stack running with `docker compose up -d`.

---

## What Phase 7 Adds

| Capability | Description |
|---|---|
| **Labour tracking** | Log hours via Telegram ("I worked 3 hours"), natural time parsing ("started at 9am ended at 2pm"), or the Hours dashboard tab. |
| **Recipe management** | Add, browse, and update production recipes with JSONB ingredients. Browse by tag in the Recipes dashboard tab. |
| **Promotions** | Create and manage discount codes (percent/fixed/BOGO), usage limits, date windows, SKU or category scope. |
| **Receipt PDF ingestion** | Upload PDF receipts directly in the Costs tab; `pypdf` extracts text and forwards to agent. |
| **Quick Ingest** | Paste a plain-text list of ingredients or supplies in the Inventory tab; agent parses it as inventory update or expense. |
| **Sales Analytics tab** | Revenue/Orders/AOV/Refunds KPIs, weekly stacked bar by channel, top products, channel split pie chart. |
| **Hours tab** | Monthly summary metrics, full time log table, inline Log Hours form. |
| **Recipes tab** | Tag-filtered recipe browser with expandable ingredient cards. |
| **Promotions tab** | Active promotions table, Create Promotion form, one-click deactivate. |

---

## Step 1 — No New Environment Variables Required

Phase 7 uses the existing database connection (`SUMMIT_PINE_DB_URL`) and agent connection (`AGENT_URL`). No new `.env` entries are needed.

---

## Step 2 — Rebuild Containers

The Streamlit UI has a new dependency (`pypdf`). Rebuild the affected images:

```bash
docker compose build summit-pine-ui open-brain-mcp agent-core
docker compose up -d
```

---

## Step 3 — Verify New Database Tables

Connect to postgres-brain and confirm the three new tables exist:

```bash
docker exec -it postgres-brain psql -U brain -d brain
```

```sql
\dt sp_*
-- Should show: sp_expenses, sp_inventory, sp_orders, sp_time_logs, sp_promotions

\dt recipes
-- Should show: recipes

-- Verify RLS is enabled
SELECT tablename, rowsecurity FROM pg_tables
WHERE tablename IN ('sp_time_logs', 'sp_promotions', 'recipes');
-- rowsecurity should be 't' for all three
```

If the tables are missing, the database init script needs to be re-run. Drop and recreate the postgres-brain volume:

```bash
docker compose down
docker volume rm my-agent_postgres-brain-data
docker compose up -d
```

> **Warning:** This wipes all brain memory. Only do this on a fresh install or if you have no data to preserve.

---

## Step 4 — Test Hours Tracking via Telegram

Send any of these messages to your bot:

```
I worked 3 hours on baking today
Started at 9am ended at 2pm making candles
Log my hours: 4 hrs, kitchen prep
```

Expected: Bot replies with `⏱️ Got it, logging your hours...` immediately, then confirms the logged entry. Verify in the dashboard Hours tab or via:

```bash
docker exec -it postgres-brain psql -U brain -d brain -c "SELECT * FROM sp_time_logs ORDER BY created_at DESC LIMIT 5;"
```

---

## Step 5 — Test Receipt PDF Upload

1. Open the Summit Pine dashboard at `http://localhost:8504`
2. Go to the **Costs** tab
3. Click **Scan Receipt**, upload any PDF receipt
4. The extracted text should appear and be forwarded to the agent for parsing

If PDF extraction fails, verify `pypdf` is installed in the container:

```bash
docker exec summit-pine-ui python3 -c "import pypdf; print('OK')"
```

---

## Step 6 — Test Quick Ingest

1. Go to the **Inventory** tab in the dashboard
2. Scroll to the **Quick Ingest** panel
3. Paste a list such as:
   ```
   - Beeswax 500g @ $8.50
   - Coconut oil 1L @ $12.00
   - Wicks x100 @ $6.00
   ```
4. Select **Expense log** from the radio buttons and click **Ingest**
5. The agent should parse the list and log expenses accordingly

---

## Step 7 — Test New Dashboard Tabs

| Tab | Quick smoke-test |
|---|---|
| **Hours** | Should display monthly summary; use Log Hours form to add a 2-hour entry and confirm it appears in the table |
| **Sales Analytics** | Revenue KPIs should show (zeros are fine on a fresh install); charts render without error |
| **Recipes** | Click **Add Recipe**, fill name + one ingredient line ("beeswax, 500, g"), submit; confirm it appears in the browser |
| **Promotions** | Create a promotion (e.g. code SAVE10, 10% percent, all products); confirm it appears in the active table |

---

## Step 8 — Test via Telegram: Recipes & Promotions

```
What recipes do we have?
Show me all active promotions
Create a promo code SPRING20 for 20% off all products, runs April 1 to April 30
```

The agent should route these through `sp_recipes` and `sp_promotions` skills automatically.

---

## Architecture Note — New Skill Signals

Three new signal patterns were added to `agent-core/app.py`:

| Pattern | Triggers | Example |
|---|---|---|
| `_SIGNAL_HOURS` | `sp_time_log` | "I worked 4 hours", "started at 9am" |
| `_SIGNAL_PROMOTIONS` | `sp_promotions` | "create a promo code", "set up a discount" |

These sit alongside the existing `_SIGNAL_INVENTORY`, `_SIGNAL_RECEIPT`, and `_SIGNAL_ORDERS` patterns — the agent picks up the right skill without the user needing to phrase requests precisely.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `sp_time_logs` table not found | Re-run DB init (see Step 3) |
| Hours form saves but Hours tab shows nothing | Check `sp_app` GRANT on `sp_time_logs` in psql |
| PDF upload shows "No text extracted" | The PDF may be image-only; use the image receipt path (pytesseract) instead |
| Promotions tab shows error | Confirm `sp_promotions` table and RLS policy exist (see Step 3) |
| `import pypdf` fails in container | Rebuild `summit-pine-ui` image (see Step 2) |

---

## What's Next (Phase 8)

- **Phase 8 (Integrations & Infrastructure):** Notion/Obsidian integration, Docker management skill (read-only first)
- **Hardware upgrade path:** RTX 3090/4090 (24 GB VRAM) unlocks qwen3-coder:30b for dramatically better coding performance
- **Mumble multi-channel routing:** Different rooms → different agent personas or capabilities

**Full details:** See `PRD.md` Phase 8 section.
