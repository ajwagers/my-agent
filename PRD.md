# My-Agent: Product Requirements Document

> **Last Updated:** 2026-03-24
> **Owner:** Andy
> **Status:** Active development — Phase 1 complete, Phase 2 complete (all chunks: 2A, 2B, 2C, 2D done). Phase 3 complete (3A, 3B, 3C done; 3D, 3E deferred). Security hardening applied post-Phase 3: Redis auth, API key on all state-changing/data-exposing endpoints, 127.0.0.1 port binding, bootstrap CLI gate, tracing sanitization hardening (URL credentials, auth headers, response previews). Phase 4A complete: skill framework with `web_search` (Tavily) and `rag_search` (ChromaDB) skills, full tool-calling loop, secret broker, and tool-calling reliability improvements (date injection, anti-hallucination prompt rules, auto-retry on refusal, richer tool descriptions). RAG embedding mismatch fixed + `rag_ingest` skill added (pre-4B patch): all ingestion and search now use ChromaDB's `DefaultEmbeddingFunction` consistently; agent can now add documents to its own knowledge base. Phase 4B complete: `url_fetch`, `file_read`, `file_write`, `pdf_parse` skills added; Redis-backed rate limiting replaces in-memory sliding window (durability across restarts). Phase 4C complete: three-layer persistent memory (Redis short-term + ChromaDB `agent_memory` long-term + working memory block in system prompt), `remember`/`recall` skills, memory sanitization with prompt-injection detection, auto-summarise truncated history, background heartbeat loop. Model & embedding upgrades (post-4C): phi4-mini replaces phi3 as DEFAULT_MODEL; qwen3:8b replaces llama3.1:8b as REASONING_MODEL and TOOL_MODEL; dedicated CODING_MODEL (qwen3:8b, consolidated from codegemma) with separate coding keyword routing; OllamaEmbeddingFunction + nomic-embed-text replaces DefaultEmbeddingFunction across all ChromaDB paths; heartbeat extended with Ollama version watcher (notifies via Telegram when Ollama updates); Telegram gateway upgraded with Redis chat queue + background worker (immediate ack, sequential processing, asyncio-safe blocking calls); 503 error handling for model-level failures. Phase 4C-Part-2 complete: Redis-backed job queue with `create_task`/`list_tasks`/`cancel_task` skills, heartbeat wired to job executor, REST job endpoints, Redis SET NX lock prevents concurrent execution. Phase 4D complete: `calculate` (AST-based safe expression evaluator) and `convert_units` (pint-backed unit converter) skills added; tool-forcing signals for math and unit queries; 467 tests total. Post-4D patch: `web_search` upgraded — Brave Search API added as primary backend (LLM Context endpoint for general queries, standard web search for URL-containing queries); Tavily retained as automatic fallback; 471 tests total. Phase 4E complete: `python_exec` (sandboxed subprocess with two-agent safety review), `calendar_read`, `calendar_write` (Outlook via MS Graph + Proton via CalDAV), `calendar_auth` (MSAL device code flow) skills added; 524 tests total. Phase 5 complete: Mumble voice/text gateway — mumble-bot container with VAD (webrtcvad), STT (faster-whisper small/CPU/int8), TTS (Piper en_US-lessac-medium), Redis queue, approval relay, notification relay; 30 new tests. Phase 5 polish (post-5 patch): TTS markdown stripping (`_strip_for_speech`), voice-concise system prompt for mumble channel, PTT-flush VAD worker, `think=False` for qwen3:8b tool dispatch (requires ollama≥0.4.7), broadened `_REALTIME_SIGNAL`/`_REFUSAL_PATTERN` to catch political/leadership queries and confident-stale answers, search result trust hardening in system prompt, full migration from sync `ollama.Client` to `AsyncClient` (eliminates connection-pool hangs in background summarisation tasks). Phase 6 complete: Open Brain MCP personal memory system (PostgreSQL + pgvector, nomic-embed-text 768-dim embeddings), identity file ingest with MD5 idempotency (SOUL.md/USER.md/IDENTITY.md/AGENTS.md auto-loaded at startup), three-layer privacy safeguard (skill execution gate + channel-aware memory middleware + system prompt directive), Mumble owner certificate authentication (cryptographic cert hash primary + username allowlist fallback, `mumble_owner` channel for trusted voice), Telegram `/remember` command, voice "save that" capture (direct brain POST from mumble-bot), Summit Pine business assistant skills (`sp_inventory`, `sp_orders`, `sp_faq`), `memory_capture`/`memory_search` skills backed by pgvector. Post-Phase-6 reliability patch: Telegram flood control prevention (`_throttled_send` enforces 1.1 s/msg rate limit, startup greeting and all send paths catch `RetryAfter` to prevent container crash loop), job deduplication in `JobManager.create()` (recurring jobs with identical prompt+user silently return existing ID), heartbeat `_run_job()` passes `channel=job.get("channel","telegram")` to `run_tool_loop()` enabling private-channel skills (calendar_read, memory_search, etc.) in scheduled jobs. Phase 7 complete (Summit Pine Operations Expansion): receipt ingestion via PDF upload in dashboard (pypdf text extraction); plain-text inventory/expense notes ingestion via dashboard Quick Ingest panel; labour hours tracking (`sp_time_log` skill + `sp_time_logs` DB table) — log by stated hours or start/end time, natural-language parsing via `_SIGNAL_HOURS`; production recipe management (`sp_recipes` skill + existing `recipes` DB table now fully wired); promotions and discount code management (`sp_promotions` skill + `sp_promotions` DB table); sales analytics dashboard tab (weekly revenue chart, top products, channel split, AOV); Streamlit dashboard expanded from 6 to 10 tabs (Hours, Sales, Recipes, Promos added); `USERS_MANUAL.md` created as end-user reference. Phase 8A complete: Prometheus metrics instrumentation in agent-core (`metrics.py`, `/metrics` endpoint, background gauge updater), Grafana operational dashboard (12 panels: request rate, queue depth, pending approvals, skill calls, policy decisions, response time p50/p95/p99, VRAM/GPU from Ollama native metrics), auto-provisioned via `grafana/provisioning/` (datasource + dashboard JSON), Prometheus scrapes agent-core and ollama-runner every 15s, 30-day TSDB retention. Streamlit health dashboard (`dashboard/`) retained for Redis log browsing but operational monitoring moves to Grafana at port 3000. Next up: Phase 8B+ or hardware upgrade to unlock larger models.

---

## 1. Project Overview

**My-Agent** is a self-hosted, multi-interface AI agent stack running entirely on local hardware via Docker. It wraps locally-hosted LLMs (Ollama with phi4-mini for fast tasks, qwen3:8b for reasoning, tool calling, and coding tasks, and qwen2.5:14b for deep/long-context tasks) behind a central FastAPI service, with multiple frontends (CLI, Telegram bot, Streamlit web UI) and optional RAG via ChromaDB. The agent can search the web in real time via Brave Search (primary) or Tavily (fallback) and query uploaded documents via ChromaDB. Embeddings use nomic-embed-text via OllamaEmbeddingFunction for a fully self-hosted vector pipeline.

The project is inspired by the Openclaw (formerly Moltbot/Clawdbot) approach: a local-first, action-oriented AI agent that runs on your own machine, connects to your chat apps, and can eventually execute real tasks with persistent memory.

### Design Principles

- **One brain, many interfaces** - All LLM logic lives in agent-core; frontends are thin adapters
- **Local-first** - Everything runs on your hardware, no cloud API dependencies
- **Containerized** - Each service is isolated in Docker, communicating over a private bridge network
- **Incremental** - Built one capability layer at a time, from basic chat up to autonomous agent

### Target Environment

- Linux (primary), Mac, or Windows (WSL2)
- GPU optional but strongly recommended — Ollama auto-detects CUDA; partial GPU offloading when VRAM < model size
- 16 GB RAM minimum for current model stack (phi4-mini 2.5 GB, gemma4:e4b ~9.6 GB, qwen2.5:14b 9 GB)
- NVIDIA GPU with 8+ GB VRAM ideal (GTX 1070 / 8 GB runs gemma4:e4b at ~6 GB VRAM, 2 GB headroom)
- Docker and Docker Compose (NVIDIA Container Toolkit required for GPU pass-through)

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          agent_net (Docker bridge)                       │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │ ollama-runner │  │  chroma-rag  │  │    redis    │  │postgres-brain│  │
│  │ (LLM engine) │  │  (vector DB) │  │  (active)   │  │(pgvector DB)│   │
│  │  :11434 int   │  │ :8000 int    │  │  :6379 int  │  │  :5432 int  │   │
│  │  no host port │  │ :8100 host   │  │  no host    │  │  no host    │   │
│  └──────┬───────┘  └──────┬───────┘  └─────────────┘  └──────┬──────┘   │
│         │                 │                                    │         │
│         │ Ollama API      │ ChromaDB API          pgvector     │         │
│         │                 │                                    │         │
│  ┌──────┴─────────────────┴──────────┐    ┌────────────────────┴──────┐  │
│  │          agent-core               │    │      open-brain-mcp       │  │
│  │     (FastAPI - central hub)       │◄───│  (memory + identity MCP)  │  │
│  │       :8000 int & host            │    │  :8002 int & host         │  │
│  └──┬──────────────┬────────────┬────┘    └───────────────────────────┘  │
│     │              │            │                                        │
│     │ /chat        │ /chat      │ /chat                                  │
│     │              │            │                                        │
│  ┌──┴──────┐  ┌────┴─────┐  ┌──┴──────────┐  ┌────────────┐             │
│  │telegram  │  │  web-ui  │  │    CLI      │  │ mumble-bot │             │
│  │-gateway  │  │(Streamlit│  │(click, runs │  │(voice/text │             │
│  │          │  │  :8501)  │  │ in-container│  │  gateway)  │             │
│  │ no host  │  │host:8501 │  │ or host)    │  │  no host   │             │
│  │ port     │  │          │  │             │  │  port      │             │
│  └──────────┘  └──────────┘  └─────────────┘  └─────┬──────┘             │
│                                                       │                  │
│  ┌──────────────┐                              ┌──────┴──────┐           │
│  │  dashboard   │  Reads logs from Redis,      │mumble-server│           │
│  │ (Streamlit   │  probes service health        │  (Murmur)   │           │
│  │  :8502)      │                              │:64738 host  │           │
│  │ host:8502    │                              └─────────────┘           │
│  └──────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### Service Map

| Service | Container Name | Image / Build | Internal Port | Host Port | Depends On |
|---|---|---|---|---|---|
| ollama-runner | ollama-runner | `ollama/ollama:latest` | 11434 | none | - |
| postgres-brain | postgres-brain | `pgvector/pgvector:pg16` | 5432 | none | - |
| open-brain-mcp | open-brain-mcp | `./open-brain-mcp` (build) | 8002 | 8002 | postgres-brain, ollama-runner (healthy) |
| agent-core | agent-core | `./agent-core` (build) | 8000 | 8000 | ollama-runner (healthy), redis, open-brain-mcp |
| telegram-gateway | telegram-gateway | `./telegram-gateway` (build) | - | none | agent-core (healthy), redis |
| mumble-server | mumble-server | `mumblevoip/mumble-server:latest` | 64738 | 64738 (TCP+UDP) | - |
| mumble-bot | mumble-bot | `./mumble-bot` (build) | - | none | agent-core, redis |
| chroma-rag | chroma-rag | `chromadb/chroma:latest` | 8000 | 8100 | - |
| web-ui | web-ui | `./web-ui` (build) | 8501 | 8501 | agent-core, chroma-rag |
| dashboard | dashboard | `./dashboard` (build) | 8502 | 8502 | redis |
| prometheus | prometheus | `prom/prometheus:latest` | 9090 | none | - |
| grafana | grafana | `grafana/grafana:latest` | 3000 | 3000 | prometheus |
| redis | redis | `redis:alpine` | 6379 | none | - |

### Volume Mounts

| Mount | Container Path | Purpose | Agent Access |
|---|---|---|---|
| Dedicated drive (host) | `/sandbox` | Agent's playground — experiments, scripts, scratch files, daily logs | Full read/write/delete |
| Named volume or host dir | `/agent` | Identity files — SOUL.md, IDENTITY.md, USER.md, MEMORY.md | Read freely, write only with owner approval |
| Named volume `brain_data` | (postgres-brain) | PostgreSQL + pgvector data for Open Brain MCP personal memory | Managed by postgres-brain service |
| (Container filesystem) | Everything else | OS, agent-core code, config, Dockerfiles | Read-only (limited), no writes |

### Four-Zone Permission Model

All agent actions are governed by a four-zone permission model. The universal rule: **the agent can look at anything, but touching things outside the sandbox requires permission.**

```
┌─────────────────────────────────────────────────────────────┐
│  ZONE 4: External World (Web, GitHub, APIs)                 │
│  Explore freely · Act only with owner approval              │
│  Hard deny: account creation, purchases, posting as owner   │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  ZONE 3: System / Stack                               │  │
│  │  Read (limited) · Suggest changes only · Never write  │  │
│  │  Dockerfiles, compose, requirements, OS, policy.yaml  │  │
│  │                                                        │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  ZONE 2: Agent Identity (/agent)                │  │  │
│  │  │  Read freely · Write only with owner approval   │  │  │
│  │  │  SOUL.md, IDENTITY.md, USER.md, MEMORY.md       │  │  │
│  │  │                                                  │  │  │
│  │  │  ┌───────────────────────────────────────────┐  │  │  │
│  │  │  │  ZONE 1: Sandbox (/sandbox)               │  │  │  │
│  │  │  │  Full freedom (hard deny-list still       │  │  │  │
│  │  │  │  applies — no fork bombs, no exfil)       │  │  │  │
│  │  │  │                                            │  │  │  │
│  │  │  │  Build, delete, experiment, run scripts,  │  │  │  │
│  │  │  │  create projects, organize freely         │  │  │  │
│  │  │  └───────────────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

| Zone | Scope | Read | Create / Modify | Delete |
|---|---|---|---|---|
| 1 - Sandbox | `/sandbox` (dedicated drive) | Free | Free | Free (own files) |
| 2 - Identity | `/agent` (SOUL.md, etc.) | Free | Owner approval required | Owner approval required |
| 3 - System | Dockerfiles, compose, requirements, OS, agent code | Allowed (limited) | Suggest only, never write | Never |
| 4 - External | Web, GitHub, APIs, services | Explore freely (GET) | Owner approval required (POST/PUT) | Owner approval required; hard deny on irreversible/financial |

### Read vs. Act Principle (External World)

The agent can explore the internet and external services freely. Any action that **modifies state** in the outside world requires owner approval.

**Explore freely (auto-allowed):** Web search, browse websites, read documentation, read GitHub repos/issues/PRs/code, fetch public read-only APIs, check package registries.

**Act only with approval:** Create/fork a GitHub repo, open a PR or issue, post a comment, send messages outside normal Telegram replies, call write/mutate API endpoints, download and install packages.

**Hard deny (never):** Create accounts on services, accept terms of service on the owner's behalf, post publicly as the owner, make purchases or financial transactions.

### Request Flow

```
User input (Telegram / Web UI / CLI)
  → POST http://agent-core:8000/chat
    body: { message, model (optional), user_id, channel, auto_approve }
  → Load identity files from /agent (hot-reload on every request)
  → build_brain_context(message, channel): query Open Brain MCP for relevant memories
      - Searches thoughts (personal memories, captured facts) and household data
      - Private channels (telegram, cli, mumble_owner): full access including identity files and household data
      - Non-private channels (mumble, web-ui): personal/identity thoughts filtered out; household data suppressed
      - Result injected as "## Relevant Memory" block in system prompt (silent if brain unavailable)
  → build_system_prompt():
      - Prepend current date/time (UTC) as first line
      - bootstrap mode: BOOTSTRAP.md + AGENTS.md
      - normal mode: SOUL.md + AGENTS.md + USER.md
      - Inject brain context block (channel-filtered)
      - If skills registered: append Tool Usage rules (when to search, anti-hallucination rules)
      - Non-private channels: append Privacy Policy directive (personal data restricted)
  → route_model() selects model:
      - model="deep" alias → DEEP_MODEL (qwen2.5:14b, 32K ctx)
      - model="reasoning" alias → REASONING_MODEL (qwen3:8b)
      - model="code" alias → CODING_MODEL (qwen3:8b)
      - model=<specific> → use as-is (client override)
      - model=None + skills registered + coding keywords → CODING_MODEL
      - model=None + skills registered → TOOL_MODEL (qwen3:8b)
      - model=None + no skills → keyword heuristic → CODING_MODEL, REASONING_MODEL, or DEFAULT_MODEL
  → Load conversation history from Redis (per user_id)
  → Truncate history to HISTORY_TOKEN_BUDGET (skipped during bootstrap)
  → run_tool_loop() (Ollama tool-calling loop, up to MAX_TOOL_ITERATIONS):
      loop:
        → Call Ollama with messages + available tools
        → If no tool calls: return final text (with auto-retry nudge if model refused to search)
        → For each tool call:
            → policy_engine.check_rate_limit()
            → skill.validate(params)
            → approval gate (if skill.requires_approval and not auto_approve)
            → skill.execute(params) with timing
            → skill.sanitize_output(result)
            → tracing.log_skill_call(skill_name, status, duration_ms)
        → Append tool results to messages, repeat
      → If max iterations hit: ask model for final answer with gathered info
  → If bootstrap mode: extract file proposals, validate, send through approval gate
  → Save user+assistant turns to Redis (tool turns NOT saved — Ollama context only)
  → Structured tracing: log_chat_request() + log_chat_response() with tool_iterations + skills_called
  → Response JSON: { response: "...", model: "<model used>", trace_id: "<16-char hex>" }
← Frontend displays response to user
```

---

## 3. Current State of Each Service

### 3.1 ollama-runner

**Status: WORKING**

- Official `ollama/ollama:latest` Docker image
- GPU pass-through via NVIDIA Container Toolkit (`deploy.resources.reservations.devices`)
- Models:
  - `phi4-mini:latest` (3.8B params, 2.5 GB) — DEFAULT_MODEL for fast general tasks
  - `gemma4:e4b` (4.5B effective params / 26B MoE, ~9.6 GB) — REASONING_MODEL + TOOL_MODEL + CODING_MODEL; native function calling, 128K context; ~6 GB VRAM on GTX 1070
  - `qwen2.5:14b` (14B params, 9 GB) — DEEP_MODEL for long-context tasks (32K ctx)
  - `nomic-embed-text` (274 MB) — embedding model for ChromaDB (OllamaEmbeddingFunction)
- Persistent volume `ollama_data` at `/root/.ollama`
- Healthcheck: `ollama list` every 30s
- No host port exposed (internal only via `agent_net`)
- **Note:** Models must be pulled manually: `docker exec ollama-runner ollama pull <model>`
- **Version watcher:** heartbeat monitors `/api/version` and notifies via Telegram when Ollama updates (useful for tracking pre-release model support)

### 3.2 agent-core

**Status: WORKING (with policy engine, identity system, bootstrap, structured tracing, full endpoint auth coverage, bootstrap channel gate, skill framework with 27 skills: web_search + rag_search + rag_ingest + url_fetch + file_read + file_write + pdf_parse + remember + recall + create_task + list_tasks + cancel_task + calculate + convert_units + python_exec + calendar_read + calendar_write + memory_capture + memory_search + sp_inventory + sp_orders + sp_faq + sp_costs + sp_time_log + sp_recipes + sp_promotions + create_persona + list_personas + delete_persona + switch_persona, Redis-backed rate limiting, three-layer persistent memory (Redis + ChromaDB + Open Brain MCP pgvector), background heartbeat loop with Ollama version watcher, Redis-backed job queue, multi-model routing with dedicated CODING_MODEL, OllamaEmbeddingFunction + nomic-embed-text embeddings, tool-forcing signals, 503 error handling, three-layer privacy safeguard with channel-aware memory injection, and 524+ tests)**

The central hub. FastAPI service that wraps Ollama, with policy engine, approval system, identity loader, conversational bootstrap, structured JSON tracing, API key authentication on state-changing endpoints, CLI-only gate on bootstrap mode, a modular skill framework supporting Ollama tool calling, and a background heartbeat loop for autonomous monitoring.

**Files:**

| File | Purpose |
|---|---|
| `app.py` | FastAPI service with `/chat`, `/health`, `/bootstrap/status`, `/chat/history/{user_id}`, `/policy/reload`, `/approval/*` endpoints. Integrates identity loading, bootstrap proposal handling, approval gates, structured tracing, bootstrap channel gate, skill registry, and tool-calling loop. System prompt injects current date/time at the top and appends explicit tool-usage rules when skills are registered. |
| `cli.py` | Click CLI with `chat` (supports `--model`, `--reason`/`-r`, `--session`), `serve`, `bootstrap` (first-run), and `bootstrap-reset` (emergency identity wipe + redo) commands |
| `skill_runner.py` | Two public functions: `execute_skill()` (rate-limit → validate → approval gate → execute → sanitize → trace, never raises) and `run_tool_loop()` (Ollama tool-call loop with per-skill call limits, auto-retry on model refusal, returns `(text, messages, stats)`). |
| `secret_broker.py` | `get(key)` — reads env var at call time, raises `RuntimeError` if unset. LLM never sees raw credential values. |
| `skills/__init__.py` | Empty package marker |
| `skills/base.py` | `SkillMetadata` dataclass + abstract `SkillBase` class with `validate()`, `execute()`, `sanitize_output()`, `to_ollama_tool()` concrete method |
| `skills/registry.py` | `SkillRegistry` — register, get, all_skills, to_ollama_tools, `__len__`. Raises `ValueError` on duplicate name. |
| `skills/rag_ingest.py` | `RagIngestSkill` — adds text to ChromaDB using `OllamaEmbeddingFunction` (nomic-embed-text). Chunks at 800 chars (100 overlap). LOW risk, no approval, rate-limited (10/min). |
| `skills/rag_search.py` | `RagSearchSkill` — ChromaDB vector search using `OllamaEmbeddingFunction` (nomic-embed-text). LOW risk, no approval, rate-limited. Replaces old hardcoded "search docs" keyword hack. |
| `skills/web_search.py` | `WebSearchSkill` — Brave Search primary (LLM Context for general queries, standard web search for URL-containing queries), Tavily fallback. LOW risk, no approval, rate-limited (3/turn). Strips HTML, `javascript:`, `data:`, and prompt injection phrases from results. API keys via secret broker. |
| `skills/url_fetch.py` | `UrlFetchSkill` — fetch URL, extract text via BeautifulSoup. SSRF prevention (blocks private IPs, Docker hostnames). Response size limit + content sanitization. LOW risk, no approval, rate-limited. |
| `skills/file_read.py` | `FileReadSkill` — read file contents with zone enforcement via `os.path.realpath()`. No symlink escape. Blocks Zone 3+. LOW risk, no approval, rate-limited. |
| `skills/file_write.py` | `FileWriteSkill` — write files with zone enforcement. Zone 1 (sandbox): auto-allowed. Zone 2 (identity): requires owner approval. Zone 3+: denied. Rate-limited. |
| `skills/pdf_parse.py` | `PdfParseSkill` — extract text from PDFs in `/sandbox` using `pypdf`. Output truncated to 4000 chars. LOW risk, no approval, rate-limited. |
| `skills/remember.py` | `RememberSkill` — store facts/observations to ChromaDB `agent_memory` collection. Sanitizes content before storage (injection detection). LOW risk, no approval, rate-limited (15/min). |
| `skills/recall.py` | `RecallSkill` — semantic search over `agent_memory` collection. Returns results with type + age labels. LOW risk, no approval, rate-limited (20/min). |
| `skills/create_task.py` | `CreateTaskSkill` — schedule a one-shot, scheduled, or recurring job in the Redis job queue. Validates cron expressions and datetime strings. LOW risk, no approval, rate-limited (5/min). |
| `skills/list_tasks.py` | `ListTasksSkill` — list all scheduled jobs for the current user from the Redis job queue. LOW risk, no approval, rate-limited (20/min). |
| `skills/cancel_task.py` | `CancelTaskSkill` — cancel a job by ID. Removes from Redis job queue. LOW risk, no approval, rate-limited (10/min). |
| `skills/calculate.py` | `CalculateSkill` — safe AST-based expression evaluator. Whitelists 7 binary ops, 2 unary ops, 4 constants (pi/e/tau/inf), and 20 math functions. No `eval()`. Returns `{"result": value}` or `{"error": ...}`. LOW risk, no approval, rate-limited (50/min), max 5 calls/turn. |
| `skills/convert_units.py` | `ConvertUnitsSkill` — pint-backed unit converter. Handles length, mass, temperature, speed, volume, and all other pint-supported units. Typed error handling for incompatible dimensions, unknown units, and temperature offset calculus. LOW risk, no approval, rate-limited (50/min), max 5 calls/turn. |
| `skills/python_exec.py` | `PythonExecSkill` — sandboxed subprocess Python execution in /sandbox. Two-agent safety review (REASONING_MODEL) before approval. Minimal env (no API keys). Timeout 30s, temp file cleaned in finally. HIGH risk, always requires approval, rate-limited (3/min). |
| `skills/calendar_read.py` | `CalendarReadSkill` — list upcoming events from Outlook (MS Graph) or Proton (CalDAV). LOW risk, no approval, `private_channels={"telegram","cli"}` (personal data gate). Rate-limited (20/min). |
| `skills/calendar_write.py` | `CalendarWriteSkill` — create/update/delete calendar events. HIGH risk, requires_approval=True, `private_channels={"telegram","cli"}`. Rate-limited (5/min). |
| `skills/memory_capture.py` | `MemoryCaptureSkill` — store a thought/fact to Open Brain MCP pgvector store. LOW risk, no approval, rate-limited (20/min). |
| `skills/memory_search.py` | `MemorySearchSkill` — semantic search over Open Brain MCP thoughts via pgvector. LOW risk, no approval, `private_channels={"telegram","cli","mumble_owner"}`. Rate-limited (20/min). |
| `skills/sp_inventory.py` | `SpInventorySkill` — query Summit Pine inventory data. LOW risk, no approval, rate-limited (20/min). |
| `skills/sp_orders.py` | `SpOrdersSkill` — look up Summit Pine order details. LOW risk, no approval, `private_channels={"telegram","cli"}`, rate-limited (20/min). |
| `skills/sp_faq.py` | `SpFaqSkill` — retrieve Summit Pine FAQ and product info. LOW risk, no approval, rate-limited (30/min). |
| `skills/sp_costs.py` | `SummitPineCostsSkill` — expense tracking, COGS, and P&L. Actions: log_expense, list_expenses, expense_summary, batch_cogs, profit_summary. LOW risk, no approval, rate-limited (20/min). |
| `skills/sp_time_log.py` | `SummitPineTimeLogSkill` — labour hour tracking. Actions: log_hours (parse start/end time or stated hours), list_hours, time_summary. Natural-language time parsing (e.g. "started at 9am ended at 2pm"). LOW risk, no approval, rate-limited (20/min). |
| `skills/sp_recipes.py` | `SummitPineRecipesSkill` — production recipe management. Actions: add, get, list (filterable by tag), update, delete. Ingredients as [{name, amount, unit}] JSON. LOW risk, no approval, rate-limited (20/min). |
| `skills/sp_promotions.py` | `SummitPinePromotionsSkill` — discount code and promotion management. Actions: create, list (active_only default), get, update, deactivate. Discount types: percent, fixed_amount, free_shipping, buy_x_get_y. LOW risk, no approval, rate-limited (20/min). |
| `memory.py` | `MemoryStore` — ChromaDB wrapper for `agent_memory` collection. Methods: `add()`, `search()`, `get_recent()`. Separate from `rag_data`; metadata schema: `{user_id, type, source, timestamp}`. |
| `memory_middleware.py` | `build_brain_context(message, channel)` — async function that queries Open Brain MCP for relevant memories and returns a formatted context block. Channel-aware: personal thoughts (identity_file source, owner_profile/agent_soul types) are filtered out for non-private channels. Household data suppressed on non-private channels. Fails silently if brain unavailable. |
| `memory_sanitizer.py` | `sanitize(content)` — strips null bytes, control chars, HTML tags; detects 8 prompt-injection patterns (ordered: injection check BEFORE HTML strip). Raises `MemoryPoisonError(ValueError)` on detection. |
| `calendar_auth.py` | MSAL device code flow for MS Graph. Token cache at `/agent/ms_token_cache.bin`. Provides `get_access_token()` for calendar skills. |
| `heartbeat.py` | Background asyncio loop started via `@app.on_event("startup")`. Ticks every `HEARTBEAT_INTERVAL` seconds (default 60). Logs each tick via `tracing._emit`. On each tick: polls `OLLAMA_HOST/api/version`, stores last-seen version in Redis (`heartbeat:ollama_version`); publishes upgrade notification to `notifications:agent` channel when version changes (includes pull command for `WATCH_MODEL`). Fires due jobs via `_run_job()`: runs the full `run_tool_loop()` with `channel=job.get("channel","telegram")` — defaults to `"telegram"` so private-channel skills (calendar_read, memory_search) are available in scheduled jobs. Catches all exceptions to stay alive. |
| `tracing.py` | Structured JSON tracing: context vars for trace IDs, JSON log formatter, Redis log storage (`logs:all` + type-specific lists), event emitters for chat/skill/policy/approval/heartbeat, enhanced sanitization, query helper for dashboard. `log_skill_call()` captures `skill_name`, `status`, `duration_ms`. |
| `policy.yaml` | Zone rules, Redis-backed rate limits (including `rag_search`, `web_search`, `url_fetch`, `file_read`, `file_write`, `pdf_parse`, `remember`, `recall`), approval settings, denied URL patterns (mounted read-only) |
| `policy.py` | Central policy engine: 4-zone model, hard-coded deny-list, rate limiting, access checks |
| `approval.py` | Approval gate manager: Redis hash storage, pub/sub notifications, async wait, timeout, proposed_content support, tracing hooks |
| `approval_endpoints.py` | FastAPI router for approval inspection and resolution |
| `identity.py` | Identity file loader: reads SOUL.md, IDENTITY.md, USER.md, AGENTS.md, BOOTSTRAP.md from `/agent`. Builds composite system prompt. Detects bootstrap mode. Hot-reloads on every request. |
| `bootstrap.py` | Bootstrap proposal parser: extracts `<<PROPOSE:FILE.md>>` markers from LLM output, validates filenames and content, checks bootstrap completion, deletes BOOTSTRAP.md when done |
| `skill_contract.py` | Abstract `SkillBase` class (legacy stub, superseded by `skills/base.py`) |
| `agent` | Shell wrapper (`#!/bin/bash`) so `agent chat "msg"` works on PATH |
| `Dockerfile` | Python 3.12, installs deps, copies CLI to `/usr/local/bin/agent` |
| `requirements.txt` | fastapi, uvicorn, ollama, click, requests, chromadb, redis, pyyaml, pypdf, beautifulsoup4, pint |
| `tests/` | Unit tests (policy, approval, identity, bootstrap, tracing, skills, memory, heartbeat, jobs), runnable without Docker — **524 tests total** |
| `tests/test_memory.py` | 21 tests: `TestMemorySanitizer` (injection detection, HTML strip, control chars) + `TestMemoryStore` (add, search, get_recent, error propagation), all using sys.modules mocking. |
| `tests/test_heartbeat.py` | 4 tests: tick invokes tracing, exception caught (loop continues), returns asyncio.Task, cancellation raises CancelledError. |
| `tests/test_jobs.py` | Tests for job queue, create_task/list_tasks/cancel_task skills, job executor in heartbeat, and job REST endpoints. |

**API Endpoints:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/chat` | `X-Api-Key` required | Main chat endpoint. Accepts `ChatRequest` (message, model, user_id, channel, auto_approve). Loads identity, builds system prompt (with current date/time at top + tool usage rules), routes through `run_tool_loop()` with registered skills, handles bootstrap proposals. Returns `{ response, model, trace_id }`. During bootstrap mode, returns 403 for any channel other than `"cli"`. |
| GET | `/health` | None | Returns `{"status": "healthy"}`. Used by Docker healthcheck and dependent services. Must remain open. |
| GET | `/bootstrap/status` | None | Returns `{"bootstrap": true/false}`. Checks if BOOTSTRAP.md exists. |
| GET | `/chat/history/{user_id}` | `X-Api-Key` required | Retrieve conversation history for a session from Redis. |
| POST | `/policy/reload` | `X-Api-Key` required | Hot-reload policy.yaml without container restart. |
| GET | `/approval/pending` | None | List all pending approval requests. |
| GET | `/approval/{id}` | None | Check a specific approval's status. |
| POST | `/approval/{id}/respond` | `X-Api-Key` required | Resolve an approval (approve/deny). Called by telegram-gateway. |

**`ChatRequest` schema:**
```json
{
  "message": "string (required)",
  "model": "string (default: null — auto-routed by route_model())",
  "user_id": "string (optional)",
  "channel": "string (optional)",
  "auto_approve": "bool (default: false — if true, bootstrap proposals are written without approval gate)",
  "history": "list (optional — client-provided conversation history)"
}
```

**Model routing (`route_model()` + skill override):**
- `model="deep"` → resolves to `DEEP_MODEL` (qwen2.5:14b, 32K ctx)
- `model="reasoning"` → resolves to `REASONING_MODEL` (gemma4:e4b)
- `model="code"` → resolves to `CODING_MODEL` (gemma4:e4b)
- `model=<any other value>` → used as-is (client override)
- `model=null` (default) with skills registered → checked for coding keywords first, then defaults to TOOL_MODEL:
  - Coding keywords match → `CODING_MODEL` (gemma4:e4b, 128K ctx)
  - No coding keywords → `TOOL_MODEL` (gemma4:e4b)
- `model=null` with no skills → keyword heuristic: coding keywords → `CODING_MODEL`; reasoning keywords → `REASONING_MODEL`; else → `DEFAULT_MODEL`
- Coding keywords: `code`, `debug`, `implement`, `refactor`, `function`, `class`, `script`, `bug`, `fix`, `test`, `write a program/script/function/class/test`, `unit test`
- Reasoning keywords: `explain`, `analyze`, `plan`, `why`, `compare`, `reason`, `think`, `step by step`, `how does`, `what if`

**Current limitations:**
- ~~**Stateless** - Every `/chat` call is independent. No conversation history.~~ FIXED (Chunk 2B): Redis-backed conversation memory with token-budget truncation.
- ~~**No tool execution** - `tools.py` defines tools as a dict but nothing reads or executes them.~~ FIXED (Phase 4A): Full Ollama tool-calling loop with `web_search` and `rag_search` skills.
- ~~**Single model** - Always uses the model specified in the request (defaults to phi3). No routing logic.~~ FIXED (Chunk 2C + 4A): `route_model()` auto-routes; `TOOL_MODEL` (qwen2.5:14b) used for all auto-routed requests when skills are registered.
- ~~**RAG routing is keyword-based** - Checks for literal string "search docs" in the message.~~ FIXED (Phase 4A): Replaced with `rag_search` skill called by the LLM via tool calling.
- ~~**`requirements.txt` is missing `chromadb`** - Fixed: `chromadb` added to requirements.txt.~~
- **Web UI bypasses agent-core** — web-ui talks directly to Ollama via LangChain, bypassing skills and the policy engine.
- ~~**Rate limiting is in-memory only**~~ FIXED (Phase 4B): Redis-backed rate limiting replaces in-memory sliding window. Rate counters survive container restarts.

### 3.3 telegram-gateway

**Status: WORKING**

Thin adapter that receives Telegram messages, forwards to agent-core, and replies.

**Files:**

| File | Purpose |
|---|---|
| `bot.py` | Telegram bot using `python-telegram-bot` v21.5 |
| `Dockerfile` | Python 3.12-slim, installs deps, runs `bot.py` |
| `requirements.txt` | python-telegram-bot, requests, redis |

**Features:**
- **Boot greeting** via `post_init` - sends a time-aware greeting message when the stack comes up. Failure (e.g. Telegram flood control) is caught and logged — does not crash the container.
- **Chat ID filtering** - only responds to the owner's chat ID (set via `CHAT_ID` env var)
- **Redis chat queue** - incoming messages are pushed to Redis list `queue:chat` rather than blocking the handler. Worker processes jobs one at a time via `asyncio.to_thread` (non-blocking event loop). Immediate acknowledgement sent to user: `"⏳ On it..."` or `"⏳ Model is busy, you're #N in queue"` based on current queue depth + active flag (`queue:chat:active`). Ack send failure is caught so the handler never crashes.
- **Queue worker** (`_queue_worker`) - background asyncio task started in `post_init`. Pops jobs from queue, sets `queue:chat:active` flag, runs agent HTTP call in thread, cancels flag, sends response via `_throttled_send`. Falls back to non-reply send if original message was deleted. Both primary and fallback send errors are caught so the worker survives Telegram outages.
- **Rate-limited sends** (`_throttled_send`) - shared async helper used by all outgoing `send_message` calls. Enforces a 1.1 s minimum gap between messages (protected by `asyncio.Lock`) to stay within Telegram's ~1 msg/sec per-chat limit and prevent flood control bans.
- **Auto-routing** - does not send a model to agent-core, allowing server-side auto-routing
- **Typing indicator** - refreshed every 4 seconds while worker processes a request (correctly non-blocking via `asyncio.to_thread`)
- **Message chunking** - splits long responses at line breaks/spaces to stay under Telegram's 4096 char limit
- **Approval inline keyboards** - subscribes to Redis `approvals:pending` channel, shows Approve/Deny buttons with risk-level emoji, writes resolution back to Redis hash
- **Approval catch-up** - on startup, scans for any pending approvals missed during downtime and re-sends them
- **Agent notifications** - subscribes to Redis `notifications:agent` channel and forwards messages to owner via `_throttled_send` (used by heartbeat for Ollama version update alerts and job completion/failure notifications)
- **`/remember` command** - Telegram slash command that captures a thought directly to Open Brain MCP memory. Usage: `/remember Andy prefers tea over coffee`. Pushed to Redis queue so it is processed in order with normal messages. Response: `"📝 Saving to memory..."`
- **No host ports** - outbound only to Telegram API + internal to agent-core + Redis

**Environment variables (from `.env` and `docker-compose.yml`):**
```
TELEGRAM_TOKEN=<bot token from @BotFather>
CHAT_ID=<your numeric chat ID>
AGENT_URL=http://agent-core:8000
REDIS_URL=redis://redis:6379       # For approval pub/sub (set in compose)
```

**Note:** The `.env` file contains real secrets. It must never be committed to version control.

### 3.4 chroma-rag

**Status: WORKING (infrastructure only)**

- Official `chromadb/chroma:latest` Docker image
- Persistent volume `chroma_data` at `/chroma/chroma`
- Internal port 8000, host port 8100
- Runs via `chroma run --host 0.0.0.0 --port 8000`
- Two collections:
  - `rag_data` — document knowledge base used by `rag_ingest` / `rag_search` skills and web UI
  - `agent_memory` — agent's personal long-term memory used by `remember` / `recall` skills; scoped per `user_id`
  - Both collections use `OllamaEmbeddingFunction` with `nomic-embed-text` (served by `ollama-runner`) — fully self-hosted, no external embedding API calls

### 3.5 web-ui

**Status: WORKING (with known issue)**

Streamlit-based chat UI with RAG capabilities.

**Files:**

| File | Purpose |
|---|---|
| `app.py` | Full Streamlit app (~442 lines) |
| `Dockerfile` | Python 3.12-slim, system deps for Chroma, runs Streamlit |
| `requirements.txt` | streamlit, ollama, langchain stack, chromadb, requests |

**Features:**
- Sidebar with: Ollama host config, model selector, temperature/top_p/max_tokens sliders, frequency/presence penalty, typing speed
- Storage options: Local ChromaDB, Remote ChromaDB, No Embeddings
- Chat persistence: save/load named conversations via ChromaDB
- RAG panel: upload text files (txt, md, py, js, html, css, json, yaml, yml) or paste text manually
- Streaming responses via LangChain `ChatOllama` with custom `StreamHandler`
- Regenerate response button, clear chat, start new chat

**Previously known issue (FIXED):** The Dockerfile CMD previously referenced `ollama-streamlit-chat_v0.7.py`. It now correctly points to `app.py`.

**Note:** The web UI talks directly to Ollama via LangChain (not through agent-core) for chat. It uses agent-core's AGENT_URL env var but doesn't currently call it. This is a design inconsistency — ideally all chat should route through agent-core.

### 3.6 dashboard

**Status: WORKING**

Streamlit-based health dashboard providing real-time operational visibility.

**Files:**

| File | Purpose |
|---|---|
| `app.py` | Streamlit app with 5 panels: System Health, Activity, Queue & Jobs, Recent Activity Feed, Security & Audit. Auto-refreshes every 10s. |
| `redis_queries.py` | Redis data access: log queries, activity aggregation, approval scanning, security event filtering |
| `health_probes.py` | HTTP health probes for all 6 services (3s timeout each) |
| `Dockerfile` | Python 3.12-slim, Streamlit on port 8502 |
| `requirements.txt` | streamlit, redis, requests |
| `tests/` | 31 unit tests (redis queries + health probes), no Docker needed |

**Features:**
- Green/yellow/red status indicators for each service
- Ollama shows loaded models, Redis shows memory usage, ChromaDB shows collection count
- Request counts (24h and 1h), broken down by channel with bar charts
- Skill execution counts with bar charts
- Average response time per model
- Policy decision summary (allowed/denied/needs approval)
- Pending approval queue display
- Filterable activity feed (by event type, count, channel)
- Security panel: policy denials + approval history
- Auto-refresh configurable via `REFRESH_INTERVAL` env var (default 10s)

**Access:** `http://localhost:8502`

### 3.7 redis

**Status: WORKING**

- `redis:alpine` image in docker-compose.yml
- Connected to `agent_net`
- `restart: unless-stopped`
- **Password-protected** via `--requirepass ${REDIS_PASSWORD}`. All services connect via `redis://:${REDIS_PASSWORD}@redis:6379` — no unauthenticated access.
- Used by agent-core for conversation history storage (per user_id session keys)
- Used by agent-core + telegram-gateway for approval gate (hash storage + pub/sub)
- Used by agent-core for structured log storage (`logs:all` firehose + `logs:chat`, `logs:skill`, `logs:policy`, `logs:approval` type-specific lists)
- Used by agent-core for Redis-backed job queue (`jobs:*` hash keys) and heartbeat job executor
- Used by mumble-bot for voice/text job queue (`queue:mumble`) and progress tick pub/sub

---

### 3.8 postgres-brain

**Status: WORKING**

- `pgvector/pgvector:pg16` image — PostgreSQL 16 with the pgvector extension for vector similarity search
- Connected to `agent_net`, no host port (internal only)
- Persistent volume `brain_data` at `/var/lib/postgresql/data`
- Database: `brain`, user: `brain`, password via `BRAIN_POSTGRES_PASSWORD`
- Used exclusively by `open-brain-mcp` service

---

### 3.9 open-brain-mcp

**Status: WORKING**

FastAPI service providing a personal semantic memory store backed by PostgreSQL + pgvector with nomic-embed-text 768-dim embeddings. Implements the Model Context Protocol (MCP) SSE endpoint in addition to a plain REST API.

**Features:**
- **Personal memory store** — stores `thoughts` rows with content, 768-dim vector embedding, source, metadata, and timestamps. Vector similarity search via pgvector HNSW indexes.
- **Household data** — structured tables for `household_facts` (key-value facts about home, family, preferences), `calendar_events`, `locations`, and `notes`.
- **Identity file ingest** — startup background task reads SOUL.md, USER.md, IDENTITY.md, AGENTS.md from `/agent/` (read-only mount). MD5 hash idempotency: only re-embeds when file changes. Creates/updates rows in `thoughts` with `source='identity_file'` and metadata `{type, file, hash}`.
- **`POST /tools/reingest_identity`** — force re-ingest all identity files without restart.
- **`POST /tools/capture_thought`** — add a new thought to memory (used by `/remember` Telegram command and voice "save that").
- **`POST /tools/recall`** — semantic search over thoughts. Returns top-N results sorted by cosine similarity.
- **MCP SSE endpoint** — `/mcp` SSE stream for Model Context Protocol tool use (for future MCP client integration).
- **Channel-aware access** — personal thoughts and household data are only injected into agent-core context for private channels (telegram, cli, mumble_owner).

**Port:** `127.0.0.1:8002:8002` (host-accessible for diagnostics; agent-core reaches it via `http://open-brain-mcp:8002`)

---

### 3.10 mumble-server

**Status: WORKING**

- `mumblevoip/mumble-server:latest` (Murmur) — the Mumble VoIP server
- Ports `64738:64738` TCP (control channel) and `64738:64738/udp` (voice traffic)
- Persistent volume `mumble_data` at `/data`
- SuperUser password via `MUMBLE_SUPERUSER_PASSWORD` env var
- Optional server password via `MUMBLE_SERVER_PASSWORD` (if set, clients must enter it to join)

---

### 3.11 mumble-bot

**Status: WORKING**

Python bot that bridges Mumble voice/text chat to agent-core. Same gateway pattern as telegram-gateway.

**Files:**

| File | Purpose |
|---|---|
| `bot.py` | Main bot: pymumble_py3 connection, VAD callbacks, STT/TTS pipeline, Redis queue worker, approval relay, agent-notification relay, owner auth, "save that" capture |
| `vad.py` | `VoiceActivityDetector` — webrtcvad frame buffering, silence-timeout detection, PTT-flush worker (runs every 300ms) |
| `stt.py` | `SpeechToText` — faster-whisper small/CPU/int8, WAV decode, transcription with confidence filter |
| `tts.py` | `TextToSpeech` — Piper en_US-lessac-medium.onnx, `_strip_for_speech()` removes all markdown before synthesis |
| `Dockerfile` | Python 3.12-slim + system audio deps + Piper + Whisper small (pre-downloaded at build) |
| `requirements.txt` | pymumble_py3, faster-whisper, pyaudio, webrtcvad, requests, redis |
| `tests/` | 30 tests: 10 VAD + 5 STT + 5 TTS + 10 bot |

**Features:**
- **Voice Activity Detection** — webrtcvad (mode 3) with configurable silence timeout. PTT-flush worker runs every 300ms to emit audio that didn't hit silence timeout (handles PTT releases).
- **Mumble owner authentication** — two-layer trust:
  - **Certificate hash (primary):** `MUMBLE_OWNER_CERT_HASH` env var — cryptographically unforgeable via pymumble_py3 UserState `hash` field. If set, only the user with this cert fingerprint gets `channel="mumble_owner"`.
  - **Username allowlist (fallback):** `MUMBLE_OWNER_USERNAMES` — used when cert hash is not yet configured.
  - Hash discovery: on first connect, bot logs `MUMBLE_OWNER_CERT_HASH=<hash>` when owner matches by username — copy this value to `.env` for stronger auth.
- **`mumble_owner` channel** — trusted Mumble users (owner cert or allowlist) are tagged as `channel="mumble_owner"`, giving them the same full private data access as Telegram. Untrusted users get `channel="mumble"` (public, personal data restricted).
- **Voice "save that"** — regex detects "save that"/"remember that"/"add that to memory" in voice transcript. Directly POSTs last bot response to `BRAIN_URL/tools/capture_thought` (bypasses agent-core for speed).
- **Progress ticks** — `_call_agent_with_progress()` sends Mumble text message ticks at 30s, 90s, 180s while agent is thinking.
- **Voice system prompt** — when channel is `mumble` or `mumble_owner`, appends concise voice guidelines: 1-4 sentences, plain prose, no markdown.
- **`_strip_for_speech()`** — strips all markdown (code blocks, bullets, headers, bold/italic, HTML) before Piper TTS.

---

## 3.12 Privacy Safeguards (Channel Trust Model)

Personal data is restricted to channels where the owner's identity is verified. Three independent layers ensure no single point of failure.

### Trusted Channels (Private)

| Channel | Source | Trust Level | Data Access |
|---|---|---|---|
| `telegram` | Telegram gateway (filtered by CHAT_ID) | Owner-verified | Full: personal data, identity files, household facts, calendar |
| `cli` | CLI running in container (requires host access) | Owner-verified | Full |
| `mumble_owner` | Mumble bot (cert hash or username allowlist match) | Owner-verified | Full |
| `mumble` | Mumble bot (untrusted/guest user) | Unverified | Public only |
| (others) | web-ui, future gateways | Unverified | Public only |

### Three Layers

**Layer 1 — Skill Execution Gate** (`skill_runner.py`): Skills with `private_channels` set will immediately return an error message (no data fetched) if the request channel is not in the allowed set. Applies to: `calendar_read`, `calendar_write`, `recall`, `memory_search`, `sp_orders`.

**Layer 2 — Memory Middleware** (`memory_middleware.py`): `build_brain_context()` filters results by channel before injecting into the system prompt:
- Non-private channels: identity_file thoughts suppressed (SOUL.md, USER.md, IDENTITY.md, AGENTS.md)
- Non-private channels: thoughts with personal metadata types (`owner_profile`, `agent_soul`, `agent_identity`, `agent_directives`) suppressed
- Non-private channels: household data API call skipped entirely

**Layer 3 — System Prompt Directive** (`app.py`): For non-private channels, an explicit privacy policy block is appended to the system prompt naming exactly what must never be shared (owner details, calendar, household facts, memory, identity files, business order details) and providing the canonical refusal phrase.

---

## 4. File Tree (current)

```
my-agent/
├── docker-compose.yml          # Orchestrates all 6 services
├── .env                        # Secrets: TELEGRAM_TOKEN, CHAT_ID, AGENT_URL
│                                 *** NEVER COMMIT THIS FILE ***
├── agent-core/
│   ├── Dockerfile              # Python 3.12, CLI on PATH
│   ├── requirements.txt        # fastapi, uvicorn, ollama, click, requests, chromadb, redis, pyyaml
│   ├── app.py                  # FastAPI: /chat, /health, /metrics (Prometheus), /bootstrap/status, /chat/history, /policy/reload, /approval/*; _update_gauges() background task
│   ├── cli.py                  # Click CLI: chat, serve commands
│   ├── skill_runner.py         # execute_skill() pipeline + run_tool_loop() Ollama tool-call driver
│   ├── secret_broker.py        # get(key) — reads env var at call time, never exposes to LLM
│   ├── memory.py               # MemoryStore — ChromaDB agent_memory wrapper (add, search, get_recent)
│   ├── memory_sanitizer.py     # sanitize() — strips control chars/HTML, detects prompt injection, raises MemoryPoisonError
│   ├── heartbeat.py            # Background asyncio heartbeat loop (start_heartbeat, _tick, configurable interval). _run_job() passes channel=job.get("channel","telegram") enabling private-channel skills in scheduled jobs.
│   ├── skills/
│   │   ├── __init__.py         # Package marker
│   │   ├── base.py             # SkillMetadata dataclass + abstract SkillBase class
│   │   ├── registry.py         # SkillRegistry: register, get, to_ollama_tools, __len__
│   │   ├── rag_ingest.py       # RagIngestSkill — add text to ChromaDB (OllamaEmbeddingFunction/nomic-embed-text, chunked)
│   │   ├── rag_search.py       # RagSearchSkill — ChromaDB vector search (OllamaEmbeddingFunction/nomic-embed-text)
│   │   ├── web_search.py       # WebSearchSkill — Brave Search primary (LLM Context/web), Tavily fallback, output sanitization, prompt injection guards
│   │   ├── url_fetch.py        # UrlFetchSkill — HTTP fetch + HTML extraction, SSRF prevention
│   │   ├── file_read.py        # FileReadSkill — zone-aware file read (no symlink escape, Zone 3+ denied)
│   │   ├── file_write.py       # FileWriteSkill — zone-aware file write (sandbox: auto; identity: approval; system: deny)
│   │   ├── pdf_parse.py        # PdfParseSkill — extract text from PDFs in /sandbox via pypdf
│   │   ├── remember.py         # RememberSkill — store facts to agent_memory (sanitized, rate-limited 15/min)
│   │   ├── recall.py           # RecallSkill — semantic search over agent_memory (rate-limited 20/min)
│   │   ├── create_task.py      # CreateTaskSkill — schedule one-shot/scheduled/recurring jobs in Redis queue
│   │   ├── list_tasks.py       # ListTasksSkill — list scheduled jobs for current user
│   │   ├── cancel_task.py      # CancelTaskSkill — cancel a job by ID
│   │   ├── calculate.py        # CalculateSkill — AST-based safe math evaluator (no eval(), whitelisted functions)
│   │   ├── convert_units.py    # ConvertUnitsSkill — pint unit converter (length, mass, temp, speed, volume, etc.)
│   │   ├── python_exec.py      # PythonExecSkill — sandboxed subprocess execution, two-agent safety review, HIGH risk
│   │   ├── calendar_read.py    # CalendarReadSkill — list Outlook/Proton calendar events, private_channels gate
│   │   ├── calendar_write.py   # CalendarWriteSkill — create/update/delete calendar events, requires_approval, private_channels gate
│   │   ├── memory_capture.py   # MemoryCaptureSkill — store thought to Open Brain MCP pgvector
│   │   ├── memory_search.py    # MemorySearchSkill — semantic search over Open Brain MCP thoughts, private_channels gate
│   │   ├── sp_inventory.py     # SpInventorySkill — Summit Pine inventory lookup
│   │   ├── sp_orders.py        # SpOrdersSkill — Summit Pine order lookup, private_channels gate
│   │   ├── sp_faq.py           # SpFaqSkill — Summit Pine FAQ and product info
│   │   ├── sp_costs.py         # SummitPineCostsSkill — expense tracking, COGS, P&L
│   │   ├── sp_time_log.py      # SummitPineTimeLogSkill — labour hour tracking (log_hours/list_hours/time_summary)
│   │   ├── sp_recipes.py       # SummitPineRecipesSkill — production recipe CRUD
│   │   └── sp_promotions.py    # SummitPinePromotionsSkill — discount code and promotion management
│   ├── calendar_auth.py        # MSAL device code flow for MS Graph token cache (/agent/ms_token_cache.bin)
│   ├── job_manager.py          # JobManager — Redis-backed job queue (CRUD, due-job query, lock management). Deduplicates recurring jobs: create() returns existing ID if identical prompt+user already active.
│   ├── job_endpoints.py        # REST router: GET /jobs, GET /jobs/{id}, DELETE /jobs/{id}
│   ├── memory_middleware.py    # build_brain_context() — channel-aware brain query; filters personal thoughts for non-private channels
│   ├── metrics.py              # Prometheus metrics: Counters (chat, skill, policy, approval), Histogram (response_ms), Gauges (queue_depth, pending_approvals)
│   ├── tracing.py              # Structured JSON tracing: context vars, event emitters, Redis log storage; increments Prometheus counters/histograms at emit time
│   ├── policy.yaml             # Zone rules, Redis-backed rate limits (all 27 skills), approval config (read-only mount)
│   ├── policy.py               # Central policy engine (zones, deny-list, rate limits)
│   ├── approval.py             # Approval gate manager (Redis hash + pub/sub + proposed_content + tracing hooks)
│   ├── approval_endpoints.py   # REST router: /approval/pending, /{id}, /{id}/respond
│   ├── identity.py             # Identity file loader, system prompt builder, bootstrap detection
│   ├── bootstrap.py            # Bootstrap proposal parser, validator, completion checker
│   ├── skill_contract.py       # Abstract SkillBase (legacy stub, superseded by skills/base.py)
│   ├── agent                   # Shell wrapper for CLI on PATH
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py         # FakeRedis mock (with list ops), policy_engine & approval_manager fixtures
│       ├── test_policy.py      # 51 tests: deny-list, zones, external access, rate limits
│       ├── test_approval.py    # 13 tests: create, resolve, timeout, get_pending
│       ├── test_identity.py    # Identity loader tests: bootstrap detection, file loading, prompt building
│       ├── test_bootstrap.py   # Bootstrap parser tests: proposal extraction, validation, completion, approval integration
│       ├── test_tracing.py     # 55 tests: trace context, JSON format, chat/skill/policy/approval logging, retention, resilience, sanitization
│       ├── test_skills.py      # 207+ tests: SkillBase, SkillRegistry, execute_skill pipeline, all 21 skills, SecretBroker, run_tool_loop
│       ├── test_memory.py      # 21 tests: MemorySanitizer (injection detection, HTML, control chars) + MemoryStore (add, search, get_recent)
│       ├── test_heartbeat.py   # 4 tests: tick→tracing, exception caught, returns Task, cancellation propagates
│       └── test_jobs.py        # Tests for job queue, task skills, job executor in heartbeat, REST endpoints
│
│
├── agent-identity/             # Bind-mounted to /agent in container (Zone 2)
│   ├── SOUL.md                 # Agent personality prompt (written during bootstrap)
│   ├── IDENTITY.md             # Structured fields: name, nature, vibe, emoji
│   ├── USER.md                 # Owner profile: name, preferences, timezone
│   └── AGENTS.md               # Operating instructions (static rules)
│                               # Note: ms_token_cache.bin (MSAL token) lives here but is .gitignored
│
├── telegram-gateway/
│   ├── Dockerfile              # Python 3.12-slim
│   ├── requirements.txt        # python-telegram-bot, requests, redis
│   └── bot.py                  # Telegram bot: Redis chat queue + background worker, immediate ack, greeting, typing, chunking, approval callbacks, agent notifications, /remember command. _throttled_send() enforces 1.1 s/msg rate limit; all send paths catch RetryAfter to prevent crash loops.
│
├── mumble-bot/
│   ├── Dockerfile              # Python 3.12-slim + audio deps + Piper + Whisper small (pre-downloaded)
│   ├── requirements.txt        # pymumble_py3, faster-whisper, pyaudio, webrtcvad, requests, redis
│   ├── bot.py                  # Main bot: Mumble connection, VAD callbacks, STT/TTS pipeline, Redis queue worker, owner auth (cert hash + username), "save that" capture
│   ├── vad.py                  # VoiceActivityDetector: webrtcvad frame buffering, silence timeout, PTT-flush worker
│   ├── stt.py                  # SpeechToText: faster-whisper small/CPU/int8, WAV decode, transcription
│   ├── tts.py                  # TextToSpeech: Piper en_US-lessac-medium.onnx, _strip_for_speech() markdown stripper
│   └── tests/                  # 30 tests: 10 VAD + 5 STT + 5 TTS + 10 bot
│
├── open-brain-mcp/
│   ├── Dockerfile              # Python 3.12-slim, installs deps, runs uvicorn main:app
│   ├── requirements.txt        # fastapi, uvicorn, asyncpg, pgvector, ollama, pyyaml
│   ├── main.py                 # FastAPI: /tools/capture_thought, /tools/recall, /tools/reingest_identity, /mcp SSE endpoint; startup identity ingest task
│   └── db.py                   # asyncpg connection pool, schema init (thoughts, household_facts, locations, notes tables + HNSW indexes)
│
├── dashboard/
│   ├── Dockerfile              # Python 3.12-slim, Streamlit on port 8502
│   ├── requirements.txt        # streamlit, redis, requests
│   ├── app.py                  # Health dashboard: 5 panels, auto-refresh
│   ├── redis_queries.py        # Redis data access & aggregation layer
│   ├── health_probes.py        # HTTP health probes for all services
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py         # FakeRedis fixture
│       ├── test_redis_queries.py  # 20 tests: log queries, activity stats, approvals, security events
│       └── test_health_probes.py  # 11 tests: healthy/unhealthy probes for each service
│
├── web-ui/
│   ├── Dockerfile              # Python 3.12-slim + system deps
│   ├── requirements.txt        # streamlit, langchain, chromadb
│   └── app.py                  # Streamlit chat UI with RAG
│
├── ollama/                     # Empty directory (placeholder)
│
├── prometheus/
│   └── prometheus.yml          # Scrape config: agent-core:8000/metrics + ollama-runner:11434/metrics, 15s interval, 30d retention
├── grafana/
│   └── provisioning/
│       ├── datasources/
│       │   └── prometheus.yaml # Auto-provision Prometheus datasource
│       └── dashboards/
│           ├── provider.yaml   # Dashboard file provider config
│           └── agent.json      # 12-panel "Agent Health" starter dashboard (pre-loaded at boot)
├── SETUP_GUIDE.md              # Full setup walkthrough for new users (Phase 1 stack)
├── SETUP_GUIDE_2.md            # Policy engine, guardrails & identity bootstrap setup guide
├── SETUP_GUIDE_3.md            # Observability & structured tracing setup guide
├── SETUP_GUIDE_4.md            # Persistent memory, heartbeat & recall setup guide (Phase 4C)
├── SETUP_GUIDE_5.md            # Calculator & unit conversion skills setup guide (Phase 4D)
├── SETUP_GUIDE_6.md            # python_exec, calendar, Brave Search setup guide (Phase 4E)
├── SETUP_GUIDE_7.md            # Mumble voice gateway setup guide (Phase 5 polish)
├── SETUP_GUIDE_8.md            # Open Brain MCP personal memory, privacy safeguards, Mumble cert auth (Phase 6 + 7)
├── SETUP_GUIDE_9.md            # Grafana + Prometheus monitoring infrastructure (Phase 8A)
├── VIDEO_OUTLINE.md            # YouTube video 1 outline (foundation stack)
├── VIDEO_OUTLINE_2.md          # YouTube video 2 outline (guardrails + identity/bootstrap)
├── VIDEO_OUTLINE_3.md          # YouTube video 3 outline (observability + tracing)
├── VIDEO_OUTLINE_4.md          # YouTube video 4 outline (persistent memory + heartbeat)
├── VIDEO_OUTLINE_5.md          # YouTube video 5 outline (calculator + unit conversion + jobs)
├── VIDEO_OUTLINE_6.md          # YouTube video 6 outline (python_exec + calendar + Brave Search)
├── VIDEO_OUTLINE_7.md          # YouTube video 7 outline (Mumble voice gateway + Phase 5 polish)
├── VIDEO_OUTLINE_8.md          # YouTube video 8 outline (Open Brain MCP + privacy + Mumble cert auth)
├── VIDEO_OUTLINE_9.md          # YouTube video 9 outline (Grafana + Prometheus monitoring dashboard)
└── PRD.md                      # This document
```

---

## 5. Known Issues / Tech Debt

| # | Issue | Severity | Location | Description | Status |
|---|---|---|---|---|---|
| 1 | ~~Missing chromadb in agent-core requirements~~ | **High** | `agent-core/requirements.txt` | `app.py` imports `chromadb` but it wasn't listed as a dependency. | FIXED |
| 2 | ~~Web UI Dockerfile references wrong file~~ | **High** | `web-ui/Dockerfile:17` | CMD referenced `ollama-streamlit-chat_v0.7.py` but file is `app.py`. | FIXED |
| 3 | ~~Redis not on agent_net~~ | **Medium** | `docker-compose.yml:79-81` | Redis service is missing `networks: [agent_net]`. Other services can't reach it. | FIXED |
| 4 | Web UI bypasses agent-core | **Medium** | `web-ui/app.py` | Web UI talks directly to Ollama via LangChain instead of routing through agent-core's `/chat`. Means any future agent-core features (memory, tools, routing) won't apply to web UI users. | OPEN |
| 5 | ~~Stale compose comments~~ | **Low** | `docker-compose.yml:27,29` | Comments say "MISSING" but the features are actually present. | FIXED |
| 6 | ~~Env var mismatch in telegram bot~~ | **High** | `telegram-gateway/bot.py:17` | Code read `YOUR_CHAT_ID` but `.env` defines `CHAT_ID`. Bot crashed on boot trying to send greeting to chat ID 0. | FIXED |
| 7 | ~~Duplicate /chat route in agent-core~~ | **High** | `agent-core/app.py` | Two `@app.post("/chat")` handlers. Merged into one with RAG routing. | FIXED |
| 8 | ~~Duplicate Application.builder() in telegram bot~~ | **Medium** | `telegram-gateway/bot.py` | App was built twice; second build overwrote `post_init` hook so greeting never fired. | FIXED |
| 9 | ~~Stray FastAPI route in web UI~~ | **Medium** | `web-ui/app.py` | `@app.post("/chat")` decorator with no FastAPI app object. Would crash on import. | FIXED |
| 10 | ~~Port conflict: agent-core and chroma-rag~~ | **High** | `docker-compose.yml` | Both services mapped host port 8000. Changed chroma-rag to 8100. | FIXED |
| 11 | ~~`/chat` endpoint unauthenticated~~ | **High** | `agent-core/app.py` | No authentication on the main chat endpoint — any process on the network could send messages, impersonate any `user_id`, or exhaust Ollama resources. | FIXED |
| 12 | ~~`/approval/{id}/respond` unauthenticated~~ | **High** | `agent-core/approval_endpoints.py` | REST endpoint for resolving approvals had no auth — anyone who could reach port 8000 could approve or deny any pending request. | FIXED |
| 13 | ~~Redis unauthenticated~~ | **High** | `docker-compose.yml` | Redis had no password. Any container on `agent_net` could read conversation history, approval data, and all logs. | FIXED |
| 14 | ~~agent-core port bound to 0.0.0.0~~ | **Medium** | `docker-compose.yml` | Port 8000 was bound to all interfaces, exposing the agent to every device on the LAN. Changed to `127.0.0.1:8000:8000`. | FIXED |
| 15 | ~~Bootstrap mode accessible from any channel~~ | **High** | `agent-core/app.py` | When `BOOTSTRAP.md` was present, Telegram or web-ui messages could participate in the identity creation conversation, allowing remote influence over `SOUL.md`, `IDENTITY.md`, and `USER.md`. Fixed with CLI-only channel gate (HTTP 403 for all other channels). Emergency reset via `agent bootstrap-reset` requires host machine access and `RESET` confirmation. | FIXED |
| 16 | ~~Rate limiting is in-memory only~~ | **Low** | `agent-core/policy.py` | The sliding window rate limiter reset on container restart. Fixed in Phase 4B: Redis-backed rate limiting with atomic `INCR`/`EXPIRE` — counters survive restarts and are shared across processes. | FIXED |
| 17 | Web UI bypasses agent-core | **Medium** | `web-ui/app.py` | Web UI talks directly to Ollama via LangChain instead of routing through agent-core. Policy engine, rate limiting, tracing, and skills (web_search, rag_search) do not apply to web UI conversations. Deferred — will be addressed in a future phase. | DEFERRED |
| 18 | Tool-calling model hallucination | **Medium** | `agent-core/skill_runner.py` | qwen2.5:14b sometimes calls web_search correctly but then ignores the results and invents an answer from training data (especially for sports/news). Mitigated with "base your answer ONLY on search results" instructions and auto-retry on refusal, but not fully solved at the model level. | OPEN — model limitation |
| 19 | ~~RAG embedding mismatch~~ | **High** | `web-ui/app.py`, `skills/rag_search.py` | Web UI ingested via LangChain+OllamaEmbeddings; `rag_search` queried via ChromaDB's DefaultEmbeddingFunction — incompatible vector spaces causing silent search failures. Fixed: all paths now use `OllamaEmbeddingFunction` (nomic-embed-text) consistently. `rag_ingest` skill added so agent can populate its own knowledge base. | FIXED |
| 20 | ~~Model OOM crash (500 error)~~ | **High** | `agent-core/app.py` | `qwen3:30b-a3b` (22.5 GB RAM) was set as TOOL_MODEL on a 15 GB system — Ollama threw an unhandled `ResponseError`, FastAPI returned 500. Fixed: (1) try/except around `run_tool_loop()` now returns HTTP 503 with a clear message; (2) TOOL_MODEL switched to `qwen3:8b` (5.2 GB) which fits. | FIXED |
| 21 | ~~Blocking HTTP call in telegram event loop~~ | **Medium** | `telegram-gateway/bot.py` | `handle_message` called `requests.post` synchronously in an async handler — blocked the asyncio event loop, preventing the typing indicator from refreshing. Fixed: requests are now offloaded to `asyncio.to_thread` inside `_queue_worker`. | FIXED |
| 22 | ~~codegemma 503 error (no tool-calling support)~~ | **High** | `agent-core/app.py` | `codegemma:latest` does not support Ollama tool calling — any request routed to CODING_MODEL with skills registered caused a 503. Fixed: CODING_MODEL consolidated to `qwen3:8b` which supports tool calling natively and matches or exceeds codegemma on coding benchmarks. codegemma removed entirely. | FIXED |
| 23 | ~~ReadTimeout 503 on model cold start~~ | **High** | `agent-core/app.py:62` | `ollama.AsyncClient(timeout=300)` caused 503s when qwen3:8b was unloaded from VRAM (after idle period) and the cold reload + inference exceeded 300 seconds. Fixed: changed to `timeout=None` (unlimited) — Ollama's own keep-alive controls model lifetime; the agent-core client should never time out waiting for inference. | FIXED |

---

## 6. Roadmap

Based on the Openclaw capability model, the project builds up in layers from "LLM in a loop" to a continuously running, tool-rich, local-first agent with its own memory, skills, and job system. Each phase is designed to be tackled as independent work chunks.

### Openclaw Alignment

The roadmap is designed to reach feature parity with Openclaw's core architecture while maintaining a stronger security posture.

| Openclaw Pillar | Our Approach | Phase |
|---|---|---|
| Long-lived agent on your own machine | Docker Compose, `restart: unless-stopped`, heartbeat loop | 1 (done) + 4C (done) |
| Gateway architecture (one brain, many apps) | agent-core hub + thin adapter pattern | 1 (done) |
| Model-agnostic / brain-vs-muscle routing | Multi-model Ollama + keyword-based auto-routing (`route_model()`) | 2 (done) |
| Soul / Persona file | Conversational bootstrap (Openclaw-inspired) with policy-gated file writes. SOUL.md, IDENTITY.md, USER.md co-authored by agent + owner. | 2A (done) |
| Conversation memory | Redis rolling history per user/session | 2 (done) |
| Policy, guardrails, observability | Four-zone permission model, approval gates, rate limits, structured tracing, health dashboard. **Built before soul/bootstrap.** | 3A (done), 3B (done), 3C (done) |
| Modular skill system | Local `skills/` directory, hand-built or vetted, no external marketplaces. Each skill enforces its own security. | 4A (done) |
| First skills (search, RAG) | Web search (Tavily) + RAG retrieval (ChromaDB) via Ollama tool calling. Secret broker for API keys. | 4A (done) |
| More skills (files, URL fetch, PDF) | URL fetch, file read/write, PDF parse. | 4B (done) |
| Memory & scheduled tasks | Persistent memory with sanitization layer, heartbeat/cron, task management. | 4C (done) |
| Full system access (files, shell, APIs) | Four-zone model: `/sandbox` (free), `/agent` (approval), system (never), external (explore free, act with approval). Docker isolation + policy engine. | 4B-4F |
| Credential security | Secret broker pattern — LLM never sees raw credentials | 4B (done) |
| Heartbeat / observe-reason-act loop | Background event loop in agent-core that checks triggers | 4C (done) |
| Jobs & automations system | Redis-backed task queue with scheduled + event triggers | 4C-Part-2 |
| Persistent memory (notes, tasks, results) | Multi-layer: Redis (short-term) + ChromaDB (long-term) with sanitization | 4C (done) |
| Self-directed task graph / Mission Control | Agent can create/manage its own task lists and subtasks | 5 |
| Proactive behavior rules | Heartbeat + standing instructions evaluate "should I act?" | 5 |

### Security Philosophy

Openclaw's power comes from giving the agent real system access — and that's also its biggest risk. Openclaw's plugin/skill ecosystem (MCP, ClawHub, community skill directories) is a known attack surface: third-party skills can exfiltrate data, inject prompts, or escalate privileges. Our approach is deliberately more controlled:

- **Four-zone permission model** — all agent actions are classified by zone (see Architecture section above). The agent has full freedom in its sandbox (`/sandbox`), needs owner approval for identity files (`/agent`), can only suggest changes to system/stack files, and can explore the external world freely but must get approval before acting on it. This is enforced by the policy engine (Chunk 3A) at every level.
- **Read vs. Act** — the universal rule. The agent can look at anything (files, web, GitHub, APIs). But modifying state outside the sandbox always requires owner approval. This applies to both internal zones and the external world. Think of it like a kid: explore freely, but ask before touching.
- **Don't nerf capabilities** — the agent should be able to touch files, run commands, call APIs, and act autonomously within its sandbox. That's what makes it useful. The guardrails exist to contain blast radius, not to limit usefulness.
- **Sandbox by default** — all execution happens inside Docker containers on an isolated network. The agent's playground is `/sandbox` (mounted from a dedicated host drive), completely walled off from the OS. File and shell tools operate only within `/sandbox`. The agent cannot access or modify the host filesystem, OS configuration, or its own Docker infrastructure.
- **No external skill/plugin marketplaces** — we do NOT use MCP, ClawHub, or any community skill directory. All skills are local Python modules in `agent-core/skills/`, written by us or carefully vetted before inclusion. The system is modular (add a skill without editing agent-core), but every skill is a deliberate, reviewed addition.
- **Allow-lists AND hard deny-lists for shell** — shell commands require explicit whitelisting. A separate, code-enforced deny-list blocks dangerous commands (`rm -rf`, `chmod 777`, `curl | bash`, `shutdown`, `mkfs`, `dd`, `:(){ :|:& };:`, network reconfig, etc.) that can NEVER execute regardless of what the LLM requests. The deny-list is checked in Python before execution, not left to the LLM's judgment.
- **The LLM never sees secrets** — API keys and tokens are NOT passed in the LLM context window. Skills access credentials through a secret broker that injects them at execution time, and only when necessary. The LLM can say "call the GitHub API" but never sees the GitHub token. This prevents prompt injection attacks from exfiltrating credentials. Secret access frequency is monitored — unusual spikes are flagged.
- **Approval gates for high-risk actions** — destructive or irreversible operations require human confirmation via Telegram or web UI before executing. The agent asks, you approve or deny.
- **The agent cannot modify its own rules** — `policy.yaml`, Dockerfiles, `docker-compose.yml`, `requirements.txt`, and agent-core source code are all Zone 3 (system/stack). The agent can read them and suggest changes, but can never write to them. This prevents the agent from weakening its own guardrails, even under prompt injection.
- **The agent cannot autonomously rewrite its own soul** — `SOUL.md` and other identity files are Zone 2. The agent can propose edits, but every write requires owner approval via Telegram. This prevents prompt injection from permanently altering the agent's personality or boundaries.
- **Suggest upgrades, never self-upgrade** — the agent can identify improvements to its own stack (new packages, config changes, model switches) and propose them via Telegram, but cannot implement them. The owner reviews, approves, and executes stack changes.
- **Audit trail** — all skill calls, results, and decisions are logged with structured tracing so you can review what the agent did and why.
- **Per-skill security** — every skill implements its own `validate()`, `risk_level`, `rate_limit`, and `sanitize_output()`. The policy engine enforces these, but skills are responsible for knowing their own threat model. A file tool validates paths. A shell tool checks deny-lists. An API tool prevents SSRF. Security is not bolted on — it's part of the skill interface.
- **Health dashboard** — a real-time operational dashboard shows what the agent is doing, what's in the queue, how many actions have executed, and any security events. You have full visibility before granting more autonomy.
- **Security before capability** — Chunk 3A (Policy Engine) is built before Chunk 2A (Soul/Bootstrap). The guardrail framework exists before the agent gets its personality or any ability to act. The bootstrap process is the first consumer of the policy engine.

**Deferred security hardening (post-Phase 3, pre-Phase 4):**

The following items are intentionally deferred — they either have no impact until skills exist, or are addressed as part of Phase 4 design:

- ~~**Rate limiting durability** (→ Phase 4B)~~ — DONE. Redis-backed rate limiting with atomic INCR/EXPIRE. Counters survive container restarts.
- **URL deny-list bypass hardening** — Phase 4B `url_fetch` blocks private IPs (10.x, 172.16-31.x, 192.168.x) and Docker service hostnames. DNS rebinding prevention and unusual port blocking are not yet implemented. Remaining hardening deferred to Phase 4F alongside `http_api`.
- **Shell deny-list regex hardening** (→ Phase 4F) — Obfuscation-resistant deny patterns are a design requirement of `shell_exec`. No shell skill exists yet.
- ~~**Skill `sanitize_output()` enforcement** (→ Phase 4A)~~ — DONE. All skills implement `sanitize_output()`; the `execute_skill()` pipeline enforces it at execution time.
- **Container hardening** (→ Chunk 3D) — Non-root user, read-only filesystem, seccomp/AppArmor profiles. Intentionally deferred.
- **Web UI → agent-core routing** (→ Future) — Web UI currently talks directly to Ollama, bypassing the policy engine and skills. Deferred.

### Legend

- ✅ **Complete** - Built and working
- 🔧 **Partial** - Infrastructure exists but incomplete
- ⬜ **Not started**

---

### PHASE 1: Foundation (COMPLETE)

> Goal: Basic chat through multiple interfaces, all containerized.

| Layer | Capability | Status | What Exists |
|---|---|---|---|
| 1 | Basic chat loop | ✅ | `/chat` endpoint, CLI, Ollama integration |
| 15 | Unified gateway (agent-core as hub) | ✅ | FastAPI service, all frontends call it |
| 16a | Telegram adapter | ✅ | Bot with greeting, typing, chunking, chat ID filter |
| 16b | Web UI | ✅ | Streamlit with model config, streaming, chat persistence |
| 16c | CLI | ✅ | Click CLI with `agent chat` command |
| - | Docker orchestration | ✅ | Compose with healthchecks, dependency ordering, private network |
| 8a | Vector DB infrastructure | ✅ | ChromaDB running with persistent volume |

---

### PHASE 2: Memory, Identity & Intelligence (COMPLETE)

> Goal: Give the agent memory, personality, and intelligent model routing.
> Openclaw equivalents: Conversation context, Soul file, model-agnostic routing.
>
> All chunks complete. Chunk 2A was the last to be implemented (required Chunk 3A as prerequisite).

#### Chunk 2A: Soul / Conversational Bootstrap ✅

**Status: COMPLETE**

**Prerequisite: Chunk 3A (Policy Engine & Guardrails).** ✅ Done.

Inspired by Openclaw's agent bootstrapping model, the agent's identity is co-authored by the agent and owner through a guided first-run conversation. All file writes during bootstrap go through the policy engine's approval gates — the agent proposes, the owner confirms.

**What was implemented:**
- `agent-core/identity.py` — Identity file loader (~90 lines): `is_bootstrap_mode()` detects BOOTSTRAP.md presence, `load_identity()` hot-loads all five identity files on every request, `load_file()` reads with MAX_FILE_CHARS (20,000) truncation, `parse_identity_fields()` extracts structured YAML-like fields from IDENTITY.md, `build_system_prompt()` composes the system prompt (bootstrap mode: BOOTSTRAP.md + AGENTS.md; normal mode: SOUL.md + AGENTS.md + USER.md).
- `agent-core/bootstrap.py` — Bootstrap proposal parser (~70 lines): `extract_proposals()` parses `<<PROPOSE:FILENAME.md>>` / `<<END_PROPOSE>>` markers via regex, `strip_proposals()` removes markers from display text, `validate_proposal()` checks filename is in ALLOWED_FILES (SOUL.md, IDENTITY.md, USER.md only), content is non-empty, and under 10,000 chars. `check_bootstrap_complete()` deletes BOOTSTRAP.md when all three required files exist with content.
- `agent-core/app.py` — Integrated identity and bootstrap: loads identity on each `/chat` request, builds composite system prompt, detects bootstrap mode, extracts proposals from LLM response, sends each through approval gate via `handle_bootstrap_proposal()`, supports `auto_approve` flag for testing. Added `/bootstrap/status` and `/chat/history/{user_id}` endpoints. During bootstrap, history truncation is skipped to preserve full conversation context. **Bootstrap channel gate:** when bootstrap mode is active, any request with `channel != "cli"` is rejected with HTTP 403 — Telegram and web-ui are completely locked out.
- `agent-core/approval.py` — Extended with `proposed_content` field so owners can see exactly what the agent wants to write before approving.
- `agent-identity/` directory — Bind-mounted to `/agent` in container. Contains SOUL.md, IDENTITY.md, USER.md, AGENTS.md. BOOTSTRAP.md is present only during first-run (deleted on completion).
- `agent-core/tests/test_identity.py` — Tests for bootstrap detection, file loading, truncation, identity field parsing, system prompt building (bootstrap vs. normal mode).
- `agent-core/tests/test_bootstrap.py` — Tests for proposal extraction (single, multiple, malformed), stripping, validation (allowed files, empty content, oversized), bootstrap completion, and integration tests for approval-gated writes (approved and denied paths).

**Current agent identity (result of first bootstrap):**
- **Name:** Mr. Bultitude
- **Nature:** A mild-mannered brown bear
- **Vibe:** mild-mannered, helpful, proactive, wise, patient
- **Owner:** Andy Wagers (Dr. Wagers)

**Bootstrap files:**

| File | Purpose | Created By | Editable By Agent? |
|---|---|---|---|
| `BOOTSTRAP.md` | One-time first-run ritual instructions. Guides the agent through its "birth" conversation. Deleted after bootstrap completes. | Template (seeded) | Deleted when done (whitelisted) |
| `SOUL.md` | Agent personality, behavioral guidelines, boundaries, tone. The agent's "character sheet." | Co-authored during bootstrap | Propose only — owner approval required |
| `IDENTITY.md` | Structured fields: name, creature/nature, vibe, emoji. Parsed by agent-core for display purposes. | Written during bootstrap | Propose only — owner approval required |
| `USER.md` | Owner context: name, preferences, timezone, how to address them. | Written during bootstrap | Propose only — owner approval required |
| `AGENTS.md` | Operating instructions: how to use memory, daily rituals, safety defaults. | Template (static) | Propose only — owner approval required |

**The bootstrap conversation (first run):**
1. Agent detects `BOOTSTRAP.md` exists in `/agent` — enters bootstrap mode
2. Any `/chat` request with `channel != "cli"` is rejected with HTTP 403 — Telegram and web-ui cannot participate in bootstrap
3. Owner runs `agent bootstrap` from the CLI on the local machine (inside the container via `docker exec`):
   - **Phase 1 (form):** collects agent name, nature, vibe, emoji, and owner info via CLI prompts. Writes `IDENTITY.md` and `USER.md` immediately.
   - **Phase 2 (soul conversation):** interactive CLI chat with the model to define personality. Owner types "done" when ready, reviews and approves the generated `SOUL.md`.
4. `check_bootstrap_complete()` detects all three files exist → deletes `BOOTSTRAP.md`
5. On all subsequent sessions, `BOOTSTRAP.md` is absent, so the agent boots normally

**Emergency identity reset (`bootstrap-reset`):**
For situations where the agent has gone off the rails and needs a full identity wipe:
1. Owner runs `agent bootstrap-reset` from the CLI on the local machine
2. Command lists the identity files that will be deleted and requires typing exactly `RESET` to confirm
3. Deletes `SOUL.md`, `IDENTITY.md`, `USER.md` — creates `BOOTSTRAP.md`
4. Immediately runs Phase 1 + Phase 2 bootstrap flow
5. Telegram and web-ui are locked out for the duration (bootstrap channel gate)
6. Two physical barriers: (a) `docker exec` requires host machine access, (b) must type `RESET` at the terminal

**Runtime behavior (every request after bootstrap):**
- `agent-core/app.py` loads identity files from `/agent` on each request (hot-reload, no restart needed)
- `SOUL.md` content is prepended as the system message on every Ollama call
- `IDENTITY.md` fields are parsed for display (agent name, emoji in responses)
- `USER.md` is included in context so the agent knows its owner
- `AGENTS.md` provides standing operational instructions
- Files are trimmed to a configurable max character limit (default 20,000) to prevent context bloat

**Post-bootstrap SOUL.md modifications:**
- The agent can **propose** edits to SOUL.md at any time (e.g., "I've noticed you prefer concise answers — want me to update my soul file?")
- Every proposed edit is sent to Telegram for owner approval
- The agent can NEVER autonomously write to SOUL.md — this is enforced by the policy engine (Zone 2)
- This prevents prompt injection from permanently altering the agent's personality

**Key decisions made:**
- Bootstrap channel: **CLI only** — Telegram and web-ui are locked out during bootstrap mode via HTTP 403. This prevents any remote party from participating in the identity creation conversation.
- Emergency reset: `bootstrap-reset` command requires host machine access (`docker exec`) and explicit `RESET` confirmation — two deliberate barriers against accidental or remote triggering.
- Template content: Openclaw-inspired defaults, iterated after first bootstrap
- Proposal format: `<<PROPOSE:FILENAME.md>>` markers parsed by regex
- Per-agent or global: Global for now (single agent), per-agent when multi-agent is added (Phase 3E)

---

#### Chunk 2B: Conversation Memory (Redis) ✅

**Status: COMPLETE**

Redis-backed conversation memory gives the agent persistent context across messages and container restarts.

**What was implemented:**
- `redis` package added to `agent-core/requirements.txt`, `networks: [agent_net]` added to redis service in `docker-compose.yml`
- `agent-core/app.py` — Redis connection at startup (`redis.from_url()`). Per-user session keys (`chat:{user_id}`). On each `/chat` request: load history from Redis, append user message, send full history to Ollama, append assistant response, save back to Redis. History stored as a single JSON blob (list of `{role, content}` objects).
- Token-budget truncation — `HISTORY_TOKEN_BUDGET` env var (default 6000 tokens). Oldest messages are dropped from the front to fit within budget. Truncation is skipped during bootstrap mode to preserve full conversation context.
- `ChatRequest` schema includes `user_id` (defaults to `"default"`) and `channel` fields. Telegram gateway already sends both. CLI passes `user_id` via `--session` flag.
- `/chat/history/{user_id}` endpoint added to retrieve conversation history.

**Key decisions made:**
- Single JSON blob per session (not a Redis list) — simpler to load/save, token truncation operates on the full list
- Token-based truncation (not message-count) — respects the context window regardless of message length
- No session TTL yet — sessions persist indefinitely in Redis (planned for future cleanup)

---

#### Chunk 2C: Brain-vs-Muscle Model Routing ✅

**Status: COMPLETE**

Openclaw users run a strong reasoning model for planning and a cheaper/faster model for execution. This makes complex, multi-step tool use practical.

**What was implemented:**
- `llama3.1:8b` as the reasoning model, `phi3:latest` as the fast default
- `route_model()` function in `agent-core/app.py` with keyword heuristic + client override:
  - 12 reasoning keywords trigger auto-escalation (`explain`, `analyze`, `plan`, `code`, `why`, `compare`, `debug`, `reason`, `think`, `step by step`, `how does`, `what if`)
  - `model="reasoning"` alias resolves to `REASONING_MODEL`
  - Any other explicit model value is passed through as-is
  - `model=None` (default) triggers auto-routing
- `ChatRequest.model` default changed from `"phi3:latest"` to `None` (enables auto-routing)
- Response JSON now includes `"model"` field so callers know which model was used
- CLI: `--model` default changed to `None`, added `--reason`/`-r` flag
- Telegram: removed hardcoded model, auto-routes based on message content
- `docker-compose.yml`: `DEFAULT_MODEL` and `REASONING_MODEL` env vars added to agent-core

**Key decisions made:**
- `llama3.1:8b` as reasoning model (good balance of capability vs. CPU performance)
- Keyword heuristic for routing (simple, predictable, no extra LLM call overhead)
- Client override preserved (any explicit model value is respected)

---

#### Chunk 2D: Fix Remaining Known Issues

**Priority: HIGH (do alongside or before 2B)**

- ~~Add `chromadb` to `agent-core/requirements.txt`~~ DONE
- ~~Fix `web-ui/Dockerfile` CMD to reference `app.py` instead of `ollama-streamlit-chat_v0.7.py`~~ DONE
- ~~Fix env var mismatch: `YOUR_CHAT_ID` -> `CHAT_ID` in `telegram-gateway/bot.py`~~ DONE
- ~~Fix duplicate `/chat` route in `agent-core/app.py`~~ DONE
- ~~Fix duplicate `Application.builder()` in `telegram-gateway/bot.py`~~ DONE
- ~~Remove stray FastAPI route from `web-ui/app.py`~~ DONE
- ~~Fix port conflict: chroma-rag host port changed from 8000 to 8100~~ DONE
- ~~Fix CLI `chat()` missing `model` parameter~~ DONE
- ~~Add `networks: [agent_net]` to redis in `docker-compose.yml`~~ DONE
- ~~Clean up stale comments in compose file~~ DONE

---

### PHASE 3: Security, Policy & Observability

> Goal: Establish the security framework, guardrails, and visibility BEFORE giving the agent any autonomy — including its own identity. Every skill, and even the bootstrap process itself, operates within this framework.
> Openclaw equivalents: Policy, guardrails, observability.
>
> **Why this comes before everything else:** Openclaw's approach is to add capabilities first and bolt on safety later. We invert that completely. Chunk 3A was the first thing built — before the soul file, before the bootstrap conversation, before any skill. The guardrail framework exists before the agent gets its personality. Chunk 2A (Soul/Bootstrap) is the first consumer of the policy engine.
>
> **Current status:** 3A (Policy Engine), 3B (Observability & Tracing), and 3C (Health Dashboard) are complete. 3D (Container Hardening) and 3E (Multi-Tenant) are deferred. Phase 4A (skill framework), 4B (files/URL/PDF + Redis rate limiting), 4C (memory + heartbeat), 4C-Part-2 (job queue + scheduled tasks), and 4D (calculate + convert_units) are all complete. Next up: Phase 4E (execution & voice) or Phase 5 (autonomy & planning).

#### Chunk 3A: Policy Engine & Guardrails ✅

**Status: COMPLETE**

The policy engine enforces the four-zone permission model. Every action the agent takes — file writes, shell commands, API calls, identity file edits, external interactions — is checked against this engine before execution.

**What was built:**
- `agent-core/policy.yaml` — Zone rules, rate limits, approval config, denied URL patterns. Mounted read-only into the container.
- `agent-core/policy.py` — Central policy engine (~280 lines): `PolicyEngine` class with `resolve_zone()` (symlink-escape-safe via `os.path.realpath()`), `check_file_access()`, `check_shell_command()`, `check_http_access()`, `check_rate_limit()` (in-memory sliding window). Enums: `Zone`, `ActionType`, `Decision`, `RiskLevel`. Hard-coded `HARD_DENY_PATTERNS` as module-level Python constants (NOT from YAML — agent cannot weaken them).
- `agent-core/skill_contract.py` — Abstract `SkillBase` class with `SkillMetadata` dataclass. Interface for all future skills: `validate()`, `execute()`, `sanitize_output()`.
- `agent-core/approval.py` — `ApprovalManager` class: Redis hash storage at `approval:{uuid}`, pub/sub on `approvals:pending` channel, 5-minute auto-deny timeout, double-resolve protection, startup catch-up.
- `agent-core/approval_endpoints.py` — REST router: `GET /approval/pending`, `GET /approval/{id}`, `POST /approval/{id}/respond`.
- `agent-core/tests/` — 164 unit tests total (51 policy + 13 approval + 20 identity + 25 bootstrap + 55 tracing), all passing, no Docker needed. Covers: deny-list patterns, zone enforcement, symlink escape, external access, rate limiting, approval lifecycle, timeout, structured tracing, sanitization, retention.
- `telegram-gateway/bot.py` — Updated with Redis subscription, InlineKeyboardMarkup for Approve/Deny, callback handler, startup catch-up for missed approvals.
- `docker-compose.yml` — Volumes: `agent_sandbox:/sandbox`, `agent_identity:/agent`, `policy.yaml:ro`. telegram-gateway now depends on Redis.

**Full details:** See `SETUP_GUIDE_2.md` and `VIDEO_OUTLINE_2.md`.

---

#### Chunk 3B: Observability & Structured Tracing ✅

**Status: COMPLETE**

Structured JSON tracing for every agent action. Every `/chat` request, skill call, policy decision, and approval event is logged to both stdout (Docker captures) and Redis lists (dashboard reads). Per-request trace IDs correlate all events within a single request.

**What was built:**
- `agent-core/tracing.py` — Core tracing module (~250 lines): `contextvars`-based trace ID propagation (`_trace_id`, `_user_id`, `_channel`), `JSONFormatter` for single-line JSON stdout output, dual-push to Redis (`logs:all` firehose + `logs:<type>` per event type), count-based retention via `LTRIM` (1000 entries for `logs:all`, 500 per type list), 5 public event emitters (`log_chat_request()`, `log_chat_response()`, `log_skill_call()`, `log_policy_decision()`, `log_approval_event()`), `_sanitize()` redacts sensitive keys (`_SENSITIVE_KEYS` covers password, token, secret, api_key, apikey, api_secret, authorization, x-api-key) and scrubs URL credentials from all string values, `_scrub_url_credentials()` strips embedded credentials from URLs (scheme://user:pass@host → scheme://***REDACTED***@host), `response_preview` in `log_chat_response()` sanitized before logging, `_truncate()` for capping field length, `get_recent_logs()` query helper for the dashboard. All Redis writes wrapped in try/except — tracing never crashes a request.
- `agent-core/app.py` — Wired tracing: `setup_logging(redis_client)` at startup, `new_trace()` at `/chat` entry, `log_chat_request()` after model routing, `log_chat_response()` with Ollama metrics (`eval_count`, `prompt_eval_count`, `total_duration`). All 3 `print()` statements replaced with structured JSON logs. `trace_id` added to `/chat` response JSON.
- `agent-core/approval.py` — Tracing hooks: `log_approval_event(action=..., status="pending")` in `create_request()`, `log_approval_event(action=status, response_time_ms=...)` in `resolve()`. Uses lazy `from tracing import log_approval_event` with `try/except ImportError` for independence.
- `agent-core/tests/conftest.py` — FakeRedis extended with `_lists` dict and `lpush()`, `ltrim()`, `lrange()`, `llen()` methods. `keys()` and `delete()` updated to include list keys.
- `agent-core/tests/test_tracing.py` — 55 tests across 10 test classes: `TestTraceContext` (5), `TestJSONFormatter` (3), `TestChatLogging` (6), `TestSharedTraceID` (3), `TestSkillLogging` (2), `TestPolicyLogging` (3), `TestApprovalLogging` (4), `TestRedisQueryable` (5), `TestRetention` (3), `TestRedisResilience` (4), `TestSanitization` (17). All passing.

**Design decisions made:**
- **Trace ID flow:** `contextvars.ContextVar` — set once at request entry, automatically available downstream without threading through parameters
- **Redis key structure:** Dual-push to `logs:all` (firehose) AND type-specific lists (`logs:chat`, `logs:skill`, `logs:policy`, `logs:approval`) for efficient dashboard querying
- **Retention:** Count-based via `LTRIM` — 1000 for `logs:all`, 500 per type list. Simple, predictable memory usage.
- **No new dependencies:** Uses only Python stdlib (`logging`, `contextvars`, `json`, `uuid`, `time`)
- **Redis resilience:** All Redis writes wrapped in try/except. If Redis is down, stdout log still succeeds. Tracing never crashes a request.
- **`print()` replacement:** All 3 print statements in `app.py` replaced with structured JSON. `cli.py` prints stay (user-facing CLI output).

**Full details:** See `SETUP_GUIDE_3.md` and `VIDEO_OUTLINE_3.md`.

---

#### Chunk 3C: Health Dashboard ✅

**Status: COMPLETE**

A dedicated Streamlit dashboard (separate service on port 8502) showing the operational state of the entire agent stack at a glance. Auto-refreshes every 10 seconds.

**What was built:**
- `dashboard/app.py` — Streamlit dashboard (~220 lines) with 5 panels: System Health (service status with green/yellow/red indicators), Activity (request counts, channel breakdown, skill calls, response times, policy decisions), Queue & Jobs (placeholder for Phase 5 + pending approvals), Recent Activity Feed (filterable log tail), Security & Audit (policy denials + approval history).
- `dashboard/redis_queries.py` — Redis data access layer (~130 lines): `get_recent_logs()` (mirrors `tracing.get_recent_logs()` independently), `count_logs_by_type()`, `get_activity_stats()` (aggregates from `logs:all` firehose — requests by channel, skill counts, avg response time by model, policy decisions), `get_pending_approvals()` (scans `approval:*` hashes), `get_approval_history()`, `get_security_events()` (combines policy denials with approval timeouts/denials).
- `dashboard/health_probes.py` — HTTP health probes (~90 lines) with 3s timeout for each service: agent-core (`/health`), Ollama (`/api/tags` — extracts loaded models), ChromaDB (`/api/v2/heartbeat`, falling back to v1), Redis (ping + memory info), web-ui (`/_stcore/health`), telegram-gateway (always "unknown" — no health endpoint).
- `dashboard/Dockerfile` — Python 3.12-slim, matches web-ui pattern.
- `dashboard/requirements.txt` — streamlit, redis, requests (minimal dependencies).
- `dashboard/tests/` — 31 unit tests (20 redis_queries + 11 health_probes), all passing without Docker.
- `docker-compose.yml` — Added `dashboard` service on port 8502, depends on redis only.

**Design decisions made:**
- **Separate service** (not a page in web-ui) — cleaner separation, avoids bloating the chat UI
- **HTTP probes** (not Docker socket) — simpler, no additional security surface
- **Auto-refresh via `time.sleep()` + `st.rerun()`** — standard Streamlit dashboard pattern, configurable via `REFRESH_INTERVAL` env var
- **No authentication** — localhost only, consistent with the rest of the stack
- **Dashboard depends only on Redis** — starts even if other services are booting, correctly shows them as unhealthy
- **Activity stats aggregate from `logs:all` (up to 1000 entries)** — bounded, fast, no new Redis data structures needed

**Test criteria:**
- Dashboard shows green status for all running services
- Sending a chat message via Telegram shows up in the activity feed within seconds
- Skill execution counts increment in real time

---

#### Chunk 3D: Container Hardening

**Priority: MEDIUM**

**Scope:**
- Run agent-core as non-root user
- Read-only filesystem where possible, writable only in `/sandbox`, `/agent` (policy-gated), and `/tmp`
- Seccomp/AppArmor profiles
- Remove agent-core host port once all frontends are containerized
- Network segmentation: ollama-runner on its own subnet, no direct internet access

---

#### Chunk 3E: Multi-Tenant & Access Control (Optional / Future)

- User/org isolation, per-user permissions
- Per-user soul files, memory stores, and skill access
- Required only if hosting for other people or teams

---

### PHASE 4: Skills & Tool Calling

> Goal: Give the agent "hands" — the ability to do things beyond chatting. Skills are added in waves, starting with the safest (read-only external) and progressing to more powerful (shell, automation).
> Openclaw equivalents: Skills/plugins framework, system access, heartbeat, jobs, persistent memory.
>
> **Prerequisite: Chunk 3A (Policy Engine) must be complete** (it is). Every skill built here is registered against the policy engine and follows the Skill Security Contract established in Chunk 3A.
>
> **Security note:** Unlike Openclaw, we do NOT use external plugin marketplaces (MCP, ClawHub, community directories). All skills are local Python modules, written by us or carefully vetted. The system is modular but curated. Each skill implements its own input validation, risk classification, rate limiting, and output sanitization. All tool output (especially web content) is treated as adversarial and sanitized before re-entering the LLM context.

#### Chunk 4A: Skill Framework ✅

**Status: COMPLETE**

**What was implemented:**
- `agent-core/skills/base.py` — `SkillMetadata` dataclass + abstract `SkillBase` with `validate()`, `execute()`, `sanitize_output()`, `to_ollama_tool()` concrete helper. `validate()` returns `(bool, str)` tuple (reason included). Parameters defined as JSON Schema for Ollama.
- `agent-core/skills/registry.py` — `SkillRegistry` with `register()`, `get()`, `all_skills()`, `to_ollama_tools()`, `__len__()`. Raises `ValueError` on duplicate name. Callers use `registry.to_ollama_tools() or None` (empty list vs. None matters for Ollama).
- `agent-core/secret_broker.py` — `get(key)` reads env var at call time. Raises `RuntimeError` if unset/empty. No caching. LLM never sees returned values.
- `agent-core/skills/rag_search.py` — `RagSearchSkill`: ChromaDB `HttpClient` query, LOW risk, no approval, rate-limited (`rag_search` key in policy.yaml, 20/min). `sanitize_output()` joins docs, truncates at 2000 chars. Replaces hardcoded "search docs" keyword check.
- `agent-core/skills/web_search.py` — `WebSearchSkill`: Brave Search primary (Brave LLM Context endpoint for general queries; standard web search when query contains a URL), Tavily automatic fallback on any Brave failure. LOW risk, no approval, max 3 calls/turn. All three backends normalised to `{"_source", "items": [{"title", "url", "text"}]}`. `sanitize_output()` strips HTML tags, `javascript:`, `data:` URIs, and prompt injection phrases from results. Total output capped at 5000 chars; per-item cap 1000 chars (standard/Tavily) or dynamic (LLM Context). API keys via secret broker.
- `agent-core/skill_runner.py` — Two public async functions:
  - `execute_skill()`: rate-limit → validate → approval gate → execute (timed) → sanitize_output → log_skill_call. Never raises — all errors returned as strings.
  - `run_tool_loop()`: Ollama tool-calling loop with per-skill call limits, auto-retry when model refuses to use tools (detects phrases like "don't have real-time access", injects nudge message, retries once). Returns `(final_text, updated_messages, stats)`.
- `agent-core/app.py` — Wired skills: registry + tool loop. Current date/time prepended to top of system prompt. Tool usage rules block appended when skills registered. TOOL_MODEL used for all auto-routed requests. History saved clean (tool turns not persisted to Redis).
- `agent-core/policy.yaml` — Added `rag_search` rate limit (20/min).

**Post-4A patch (pre-4B): RAG embedding fix + rag_ingest skill**
- Fixed embedding mismatch: `rag_search` and the new `rag_ingest` skill both explicitly use `DefaultEmbeddingFunction` (`all-MiniLM-L6-v2` via sentence-transformers), ensuring vectors from ingestion and search are compatible. Web UI ingestion was previously using LangChain+OllamaEmbeddings — an incompatible vector space.
- `agent-core/skills/rag_ingest.py` — `RagIngestSkill`: splits text into 800-char chunks (100 overlap), stores in ChromaDB `rag_data` collection using `DefaultEmbeddingFunction`. LOW risk, no approval, rate-limited (10/min, 5 calls/turn). Agent can now add documents to its own knowledge base during a conversation.
- `web-ui/app.py` — Replaced LangChain+OllamaEmbeddings with ChromaDB native API + `DefaultEmbeddingFunction` in both `add_to_rag_database()` and `get_relevant_context()`. Removed `langchain-chroma` dependency.
- 15 new tests added for `RagIngestSkill`. Total: 244 tests.
- `docker-compose.yml` — Added `TOOL_MODEL=qwen2.5:14b`, `MAX_TOOL_ITERATIONS=5`, `TAVILY_API_KEY`.
- `agent-core/tests/test_skills.py` — 80 tests: SkillBase/Registry, execute_skill pipeline (rate limit, validation, approval, errors, tracing), RagIngestSkill, RagSearchSkill, WebSearchSkill, SecretBroker, run_tool_loop (no-tools, with-tools, per-turn limits, max iterations, auto-retry on refusal).

**Key decisions made:**
- `TOOL_MODEL=qwen2.5:14b` — better tool calling than llama3.2:latest or phi3. qwen2.5:14b serves as both TOOL_MODEL and DEEP_MODEL.
- Tool arguments may arrive as JSON string or dict — handled with `json.loads()` fallback.
- Per-skill call limits (e.g., `max_calls_per_turn=3` for web_search) prevent infinite tool loops within a single turn.
- Auto-retry nudge fires only once per request (iteration == 0 and no skills called yet) to avoid loop.
- History separation: `updated_messages` (with tool turns) used only for Ollama context within one request; Redis history stores only clean user/assistant pairs.

---

#### Chunk 4B: First Skills — Search, Files & RAG ✅

**Status: COMPLETE**

Skills that give the agent the ability to fetch URLs, read/write files, parse PDFs, and add documents to its knowledge base. Redis-backed rate limiting was also added in this phase.

| Skill | Description | Risk Level | Approval | Key Security | Status |
|---|---|---|---|---|---|
| `web_search` | Search the web via Brave Search (primary) + Tavily (fallback) | Low | No | API keys via secret broker, result sanitization, prompt injection guards, rate limited | ✅ Done (4A, upgraded post-4D) |
| `rag_search` | Query ChromaDB vector database | Low | No | `OllamaEmbeddingFunction` (nomic-embed-text), result truncation, rate limited | ✅ Done (4A + patch) |
| `rag_ingest` | Add text to ChromaDB knowledge base | Low | No | `OllamaEmbeddingFunction` (nomic-embed-text), chunked (800/100), rate limited (10/min) | ✅ Done (pre-4B patch) |
| `url_fetch` | Fetch and extract content from a URL | Low | No | SSRF prevention (block internal IPs/Docker network), denied URL patterns, response size limit, content sanitization | ✅ Done (4B) |
| `file_read` | Read file contents | Low (sandbox), Medium (identity) | No | Path validation via `resolve_zone()`, no symlink escape, Zone 3+ denied | ✅ Done (4B) |
| `file_write` | Write/create files | Low (sandbox), High (identity) | No (sandbox), Yes (identity) | Path validation, zone enforcement, identity writes require owner approval | ✅ Done (4B) |
| `pdf_parse` | Extract text from PDF files | Low | No | Parse in sandbox only, size limits, output sanitization via pypdf | ✅ Done (4B) |

**Per-skill security details:**
- **url_fetch**: Validates URL against denied patterns (paypal, stripe, billing, signup, register from policy.yaml). Blocks internal network addresses (10.x, 172.16-31.x, 192.168.x, localhost, Docker service names). Response body truncated and sanitized.
- **file_read/file_write**: `validate()` resolves the real path via `os.path.realpath()` and checks against zone rules. `../` traversal and symlink escape are caught. Identity file writes go through the approval gate.
- **pdf_parse**: Only operates on files in `/sandbox`. Uses `pypdf` (pure Python, no shell). Output truncated to prevent context bloat.
- **Redis-backed rate limiting**: Replaced in-memory sliding window with atomic Redis `INCR`/`EXPIRE`. Rate counters survive container restarts and are shared across processes.

**What was implemented:**
- `agent-core/skills/url_fetch.py` — `UrlFetchSkill`: fetches URL, extracts text via BeautifulSoup. SSRF prevention via IP/hostname deny-list. Response size cap + sanitization.
- `agent-core/skills/file_read.py` — `FileReadSkill`: zone-aware file read with `os.path.realpath()` path validation.
- `agent-core/skills/file_write.py` — `FileWriteSkill`: zone-aware file write. Zone 1 auto-allowed; Zone 2 requires approval; Zone 3+ denied.
- `agent-core/skills/pdf_parse.py` — `PdfParseSkill`: extracts text from PDFs in `/sandbox` using `pypdf`. Max 4000 chars output.
- `agent-core/policy.py` — Redis-backed rate limiting: `check_rate_limit()` now uses `redis.incr(key)` + `redis.expire(key, window)` atomically. Rate state survives restarts.
- `agent-core/policy.yaml` — Added rate limit entries for `url_fetch`, `file_read`, `file_write`, `pdf_parse`.
- `agent-core/requirements.txt` — Added `beautifulsoup4`, `pypdf`.
- 53 new tests added for the 4 new skills + Redis rate limiting. Total after 4B: **305 tests**.

**Note:** No dedicated setup guide exists for 4B — code was committed directly and is self-documenting. `SETUP_GUIDE_4.md` covers Phase 4C and lists 4B as a prerequisite.

---

#### Chunk 4C: Memory & Heartbeat ✅

**Status: COMPLETE** (jobs/scheduled tasks deferred to 4C-Part-2)

Persistent long-term memory with a prompt-injection sanitization layer, working memory injection into the system prompt, auto-summarise of truncated history, and a background heartbeat loop.

**Core problem solved:** Context windows are small (8K tokens standard). The 6K history budget covers ~15–20 turns. Once old messages are dropped, they're gone.

**Solution — three-layer memory architecture:**
1. **Short-term:** Existing Redis rolling window (unchanged)
2. **Long-term:** ChromaDB `agent_memory` collection — separate from `rag_data`, metadata schema: `{user_id, type, source, timestamp}`
3. **Working memory:** Compact auto-injected block in system prompt (`## Working Memory` section, ~150–200 tokens)

**What was implemented:**

- `agent-core/memory.py` — `MemoryStore`: ChromaDB `agent_memory` wrapper. `add(content, type, user_id)` returns memory_id. `search(query, user_id, n=5)` semantic search. `get_recent(user_id, n=8)` returns last 50 sorted by timestamp, top n. Uses `OllamaEmbeddingFunction` (nomic-embed-text, consistent vector space with `rag_data`). _(Originally implemented with `DefaultEmbeddingFunction`; upgraded post-4C to OllamaEmbeddingFunction for a fully self-hosted pipeline.)_
- `agent-core/memory_sanitizer.py` — `sanitize(content)`: strips null bytes + control chars → checks 8 injection patterns (`ignore previous instructions`, `system prompt`, `disregard instructions`, `you are now`, `new instructions:`, `</?system`, `[INST]`, `<<SYS>>`) → strips HTML tags → collapses whitespace. Critical ordering: injection check BEFORE HTML strip (prevents `<<SYS>>` bypass). Raises `MemoryPoisonError(ValueError)` on detection.
- `agent-core/heartbeat.py` — `heartbeat_loop(state)`: asyncio loop, `await asyncio.sleep(HEARTBEAT_INTERVAL)` then `_tick(state)`, catches all `Exception` (not `BaseException` — `CancelledError` propagates). `_tick()` emits heartbeat trace event. `start_heartbeat()` wraps loop in `asyncio.create_task()`.
- `agent-core/skills/remember.py` — `RememberSkill`: params `content` (max 1000 chars), `type` (fact/observation/preference, default fact). `validate()` calls `sanitize()`, returns error on `MemoryPoisonError`. `execute()` pops `_user_id` from params (injected by skill_runner), calls `MemoryStore().add()`. LOW risk, no approval, rate-limited (15/min), max 5 calls/turn.
- `agent-core/skills/recall.py` — `RecallSkill`: params `query` (max 500 chars), `n_results` (1–10, default 5). `sanitize_output()` returns numbered list `"N. [{type}, {age}] {content}"` with age formatted as "just now/5m/2h/3d/2w/1mo". LOW risk, no approval, rate-limited (20/min).
- `agent-core/skill_runner.py` — One-line change: `result = await skill.execute({**params, "_user_id": user_id})` — injects `_user_id` AFTER validation, BEFORE execute. Backward-compatible (existing skills ignore unknown keys).
- `agent-core/app.py` — Four additions:
  1. Skill registration: `RememberSkill()`, `RecallSkill()`
  2. `build_working_memory(user_id)`: calls `memory_store.get_recent(n=8)`, formats as `## Working Memory` block, 1200-char hard cap, injected between identity content and tool hints. Fails silently if ChromaDB unavailable.
  3. `_summarise_and_store(dropped, user_id)`: async fire-and-forget, summarises dropped history via Ollama (2048 ctx), stores as `type="summary"` in `agent_memory`. Triggered when history is truncated.
  4. Startup event: `start_heartbeat(app.state)`
- `agent-core/policy.yaml` — Added `remember` (15/min) and `recall` (20/min) rate limits.
- `agent-core/tests/test_memory.py` — 21 tests: `TestMemorySanitizer` (13) + `TestMemoryStore` (8). ChromaDB mocked via `patch.dict(sys.modules, ...)` for lazy-import isolation.
- `agent-core/tests/test_heartbeat.py` — 4 tests: tick invokes tracing, exception caught (loop continues), returns asyncio.Task, cancellation raises CancelledError.
- `agent-core/tests/test_skills.py` — Appended `TestRememberSkill` (14 tests) + `TestRecallSkill` (13 tests). Skills mocked via `patch("skills.remember.MemoryStore", ...)`.
- **Total after 4C: 357 tests** (up from 305).

**Key decisions made:**
- ChromaDB collection `agent_memory` is completely separate from `rag_data` (different metadata schema, different purpose). Both use `OllamaEmbeddingFunction` (nomic-embed-text) for a compatible, fully self-hosted vector space.
- Injection check runs BEFORE HTML stripping — critical to prevent `<<SYS>>` pattern from being mangled and bypassing detection.
- `CancelledError` inherits from `BaseException` in Python 3.8+ — `except Exception` in heartbeat loop correctly lets task cancellation propagate.
- Auto-summarise is fully fire-and-forget (`asyncio.create_task`) — never blocks a chat response, never crashes on failure.
- Working memory hard cap at 1200 chars (~300 tokens) — prevents working memory from eating too much of the system prompt budget.
- Jobs/scheduled tasks deferred to 4C-Part-2 — heartbeat infrastructure is in place, job queue execution not yet wired.

**Deferred to 4C-Part-2:**
- Redis-backed job queue with scheduled/event-driven/one-shot triggers
- `create_task`, `list_tasks`, `cancel_task` skills
- `POST /jobs`, `GET /jobs`, `DELETE /jobs/{id}` API endpoints
- Overlapping-execution prevention via Redis locks

**Full details:** See `SETUP_GUIDE_4.md` and `VIDEO_OUTLINE_4.md`.

---

#### Chunk 4C-Part-2: Jobs & Scheduled Tasks ✅

**Status: COMPLETE**

Redis-backed job queue with LLM-callable skills for scheduling and managing recurring tasks. The heartbeat loop is wired to the job executor.

**What was implemented:**
- `agent-core/job_manager.py` — `JobManager`: Redis-backed CRUD for jobs. Job types: one-shot, scheduled (ISO datetime), recurring (cron-style). `get_due_jobs()` returns jobs past their `next_run` time. `SET NX` lock prevents concurrent execution of the same job across ticks.
- `agent-core/job_endpoints.py` — FastAPI router: `GET /jobs` (list all), `GET /jobs/{id}` (inspect), `DELETE /jobs/{id}` (cancel). All require API key.
- `agent-core/skills/create_task.py` — `CreateTaskSkill`: schedule a job with title, type, and optional schedule/cron. Returns job ID and next_run. Rate-limited (5/min).
- `agent-core/skills/list_tasks.py` — `ListTasksSkill`: list all jobs for the current user. Formatted output with status and next_run. Rate-limited (20/min).
- `agent-core/skills/cancel_task.py` — `CancelTaskSkill`: cancel a job by ID. Returns confirmation. Rate-limited (10/min).
- `agent-core/heartbeat.py` — Extended `_tick()`: queries `job_manager.get_due_jobs()`, runs each through `run_tool_loop()` (with the job's stored message and the agent's own identity), updates `next_run` for recurring jobs, marks one-shots as complete.
- `agent-core/policy.yaml` — Added `create_task` (5/min), `list_tasks` (20/min), `cancel_task` (10/min) rate limits.
- `agent-core/tests/test_jobs.py` — Tests for job lifecycle (create, list, cancel, execute), heartbeat integration, REST endpoints.
- **Total after 4C-Part-2: 424 tests.**

**Full details:** See `SETUP_GUIDE_4.md` (Phase 4C section) and `VIDEO_OUTLINE_4.md`.

---

#### Chunk 4D: Math, Physics & Media ✅ (partial)

**Status: COMPLETE (calculator + unit conversion; image_gen deferred)**

The agent is now a reliable calculator. It never guesses arithmetic or unit conversions from training data — it always uses the `calculate` and `convert_units` tools. Tool-forcing signals detect math and unit queries and inject hard directives into the system prompt before the first LLM call.

**What was implemented:**

- `agent-core/skills/calculate.py` — `CalculateSkill`: AST-based safe expression evaluator. `ast.parse(expr, mode='eval')` + recursive `_safe_eval()` with strict whitelists:
  - **Binary ops:** Add, Sub, Mul, Div, Pow, Mod, FloorDiv
  - **Unary ops:** UAdd, USub
  - **Constants (Name nodes):** `pi`, `e`, `tau`, `inf` (from `math`)
  - **Functions (Call nodes):** `sqrt sin cos tan asin acos atan atan2 log log10 log2 exp abs ceil floor factorial degrees radians hypot round gcd`
  - All other AST node types raise `ValueError("Expression type not allowed: ...")`. No `eval()`. No imports reachable from the expression.
  - Pre-parse validation rejects `__`, `import`, `exec`, `eval`, `open`, `lambda` before AST parse.
  - Edge case: `math.isfinite()` called inside `try/except OverflowError` — `factorial(10000)` returns a Python arbitrary-precision integer that overflows on float conversion.
  - LOW risk, no approval, rate-limited (50/min), max 5 calls/turn.

- `agent-core/skills/convert_units.py` — `ConvertUnitsSkill`: pint-backed unit converter. Creates a `pint.UnitRegistry()` per call, converts `Quantity(value, from_unit).to(to_unit)`. Typed error handling:
  - `DimensionalityError` → "Cannot convert X to Y (incompatible dimensions)"
  - `UndefinedUnitError` → "Unknown unit: 'X'"
  - `OffsetUnitCalculusError` → "Use 'degC', 'degF', 'kelvin' for temperature conversions"
  - LOW risk, no approval, rate-limited (50/min), max 5 calls/turn.

- `agent-core/requirements.txt` — Added `pint`.
- `agent-core/policy.yaml` — Added `calculate` (50/min) and `convert_units` (50/min) rate limits.
- `agent-core/app.py` — Registered both skills. Extended tool-usage hint with two new rules. Added two tool-forcing signal patterns (`_SIGNAL_CALCULATE`, `_SIGNAL_CONVERT`) with corresponding directives in `_tool_forcing_directive()`.
- `agent-core/tests/test_skills.py` — Appended `TestCalculateSkill` (20 tests) + `TestConvertUnitsSkill` (18 tests → 38 tests) + edge case fixes. **Total: 467 tests.** Post-4D patch: `TestWebSearchSkill` fully replaced (17 new tests for Brave LLM Context, Brave web, Tavily fallback, both-fail error — replaces 10 old Tavily-only tests). **Total: 471 tests.**

**Deferred:**
- `image_gen` — Stable Diffusion on hold pending GPU upgrade (gemma4:e4b uses ~6 GB VRAM leaving ~2 GB headroom on the GTX 1070 — not enough for a capable image model; need 24 GB VRAM).

**Full details:** See `SETUP_GUIDE_5.md` and `VIDEO_OUTLINE_5.md`.

---

#### Chunk 4E: Execution & Voice ✅

**Status: COMPLETE**

| Skill | Description | Risk Level | Approval | Status |
|---|---|---|---|---|
| `python_exec` | Execute Python code in sandboxed subprocess in /sandbox. Two-agent safety review (REASONING_MODEL) before approval gate. Minimal env, 30s timeout. | High | Yes (always) | ✅ Done |
| `calendar_read` | List upcoming events from Outlook (MS Graph MSAL) or Proton (CalDAV). `private_channels={"telegram","cli"}`. | Low | No | ✅ Done |
| `calendar_write` | Create/update/delete calendar events. `private_channels={"telegram","cli"}`, `requires_approval=True`. | High | Yes | ✅ Done |

**Mumble Voice Interface** — complete gateway:
- mumble-server (Murmur) + mumble-bot containers
- VAD (webrtcvad mode 3) + STT (faster-whisper small/CPU/int8) + TTS (Piper en_US-lessac-medium)
- Redis queue, approval relay, progress ticks (30s/90s/180s)
- `_strip_for_speech()` strips markdown before TTS
- Voice system prompt: 1-4 sentences, plain prose, no markdown
- Owner certificate hash authentication → `mumble_owner` channel
- PTT-flush VAD worker (300ms interval)
- `think=False` + `AsyncClient` migration for qwen3:8b tool dispatch reliability
- 30 new tests (10 VAD + 5 STT + 5 TTS + 10 bot)

**Full details:** See `SETUP_GUIDE_7.md` and `VIDEO_OUTLINE_7.md`.

---

#### Chunk 4F: Shell, Git & Advanced Automation

**Priority: MEDIUM-LOW — Most powerful and dangerous skills. Built last with the most guardrails.**

| Skill | Description | Risk Level | Approval | Notes |
|---|---|---|---|---|
| `shell_exec` | Execute shell commands in agent-core container | Critical | Yes (always) | Two-layer security: hard deny-list (code-enforced) + allow-list (policy.yaml). Full command logging. |
| `git_ops` | Git operations (status, log, diff, commit, push) | High | Read: No. Write: Yes | Depends on shell access. Read ops (status, log, diff) auto-allowed. Write ops (commit, push) require approval. |
| `browser` | Browser automation with limitations | High | Yes (always) | Headless browser (Playwright/Puppeteer). Read-only by default — can navigate and extract, but form submission/clicking requires approval. Blocked on financial/signup URLs. **Approached carefully.** |
| `sql_query` | Query SQL databases | High | Read: No. Write: Yes | Details TBD — need to choose DB backend. SELECT allowed, INSERT/UPDATE/DELETE require approval. |
| `github_api` | GitHub API operations | Medium-High | Read: No. Write: Yes | Read repos/issues/PRs auto-allowed. Create/comment/merge require approval. Token via secret broker. |
| `http_api` | Generic HTTP API calls | Medium-High | GET: No. Mutating: Yes | Generic REST client. GET auto-allowed, POST/PUT/DELETE require approval. SSRF prevention. Denied URL patterns apply. |

**MCP Integration (if needed):**
- Evaluate whether any MCP servers provide genuine value that we can't build ourselves
- If used, each MCP tool is wrapped in our skill interface with full policy enforcement — MCP does NOT bypass the security model
- MCP tools are individually vetted and allow-listed, never auto-discovered

**Test criteria:**
- Shell command `ls /sandbox` succeeds; `rm -rf /` is denied by hard deny-list
- Git read ops work without approval; `git push` triggers approval gate
- Browser can fetch a page; navigation to paypal.com is denied
- SQL SELECT works; DROP TABLE is denied
- GitHub API can read repos; creating a PR requires approval

---

### PHASE 5: Autonomy & Planning

> Goal: The agent can plan, execute multi-step tasks, and learn from outcomes.
> Openclaw equivalents: Task graph, proactive behavior rules, learning from feedback.

#### Chunk 5A: Self-Critique Loops

- Multi-agent pattern: one agent plans, another critiques, an executor runs commands, iterating until criteria are met
- Uses the brain-vs-muscle model split (reasoning model for planning/critique, fast model for execution)
- Configurable iteration limit (from policy) to prevent infinite loops
- Each iteration logged via tracing

#### Chunk 5B: Self-Directed Task Graph

- The agent can create its own task lists and subtask trees
- When one task completes, it evaluates what to do next
- Enables fan-out/fan-in flows and parallel skill usage
- The `create_task` skill feeds back into the jobs system (Chunk 4C)

#### Chunk 5C: Proactive Behavior Rules

- Standing instructions in the soul file define proactive behaviors:
  - "If you notice X, do Y"
  - "Every Monday, prepare a weekly summary"
  - "Monitor system health and alert me if anything looks wrong"
- The heartbeat loop (Chunk 4C) evaluates these rules on each tick
- The agent can propose new rules, subject to user approval

#### Chunk 5D: Learning from Feedback

- Collect thumbs-up/down or explicit corrections via Telegram reactions or web UI buttons
- Store feedback in memory, use it to adjust future behavior
- Update soul file or memory entries based on patterns ("user prefers detailed code explanations")
- No heavy RL needed — simple preference tracking and prompt adjustment

---

### PHASE 6: Personal Memory, Privacy & Business Skills (COMPLETE) ✅

> Goal: Give the agent a semantic long-term memory backed by pgvector, ingest its own identity files into memory, protect personal data from leaking to unauthorized channels, secure Mumble with cryptographic owner authentication, and add Summit Pine business assistant skills.

#### Chunk 6A: Open Brain MCP — Personal Memory ✅

**Status: COMPLETE**

- `postgres-brain` service — PostgreSQL 16 + pgvector extension, `brain_data` named volume
- `open-brain-mcp` service — FastAPI MCP memory server. Tables: `thoughts` (768-dim vectors), `household_facts`, `calendar_events`, `locations`, `notes`
- `memory_capture` and `memory_search` skills in agent-core, backed by pgvector semantic search
- Brain context injected into every agent-core request via `memory_middleware.py` (`build_brain_context()`)
- Telegram `/remember` command → pushes thought directly to Open Brain via agent-core
- Voice "save that" / "remember that" → mumble-bot directly POSTs to `BRAIN_URL/tools/capture_thought`

**Identity file ingest:**
- Startup background task in `open-brain-mcp/main.py` reads SOUL.md, USER.md, IDENTITY.md, AGENTS.md from `/agent/` (read-only mount)
- MD5 hash idempotency: each file's hash stored in `metadata->>'hash'` — only re-embeds when file content changes
- `POST /tools/reingest_identity` endpoint for force-refresh without restart
- Stored with `source='identity_file'` and `metadata: {type, file, hash}` for precise filtering

#### Chunk 6B: Privacy Safeguards — Channel Trust Model ✅

**Status: COMPLETE**

Three-layer system ensuring personal data never leaks to non-private channels:

1. **Skill execution gate** (`skill_runner.py`) — `private_channels` field on `SkillMetadata`. Checked before rate limit. Returns error message immediately if channel not allowed.
2. **Memory middleware filter** (`memory_middleware.py`) — `build_brain_context()` strips identity_file thoughts and personal thought types from context before non-private-channel responses.
3. **System prompt directive** (`app.py`) — explicit Privacy Policy block injected for non-private channels naming exactly what must never be shared.

Private channels: `frozenset({"telegram", "cli", "mumble_owner"})`

#### Chunk 6C: Mumble Owner Certificate Authentication ✅

**Status: COMPLETE**

- `MUMBLE_OWNER_CERT_HASH` env var — primary trust (cryptographically unforgeable, from pymumble_py3 UserState `hash` field)
- `MUMBLE_OWNER_USERNAMES` env var — fallback trust (username allowlist, used before cert hash is configured)
- `_get_channel(username, cert_hash)` in `mumble-bot/bot.py` — returns `"mumble_owner"` for trusted, `"mumble"` for untrusted
- Hash discovery: bot logs cert hash on first connect when owner matches by username
- `mumble_owner` channel gets full private data access; `mumble` channel gets public-only access

#### Chunk 6D: Summit Pine Business Assistant Skills ✅

**Status: COMPLETE**

- `sp_inventory` — query inventory data, LOW risk, auto-allowed
- `sp_orders` — look up order details, `private_channels={"telegram","cli"}` (customer data protection)
- `sp_faq` — retrieve FAQ and product information, LOW risk, auto-allowed

---

### PHASE 7: Summit Pine Operations Expansion (COMPLETE)

> Goal: Extend the Summit Pine business assistant with full operational coverage — labour tracking, recipe management, promotions, receipt ingestion (PDF + images + plain text), and a 10-tab analytics dashboard.

#### Chunk 7A: New Skills — Time Log, Recipes, Promotions

- **`sp_time_log`** — `SummitPineTimeLogSkill` — `log_hours`, `list_hours`, `time_summary`
  - Accepts `hours` directly or `start_time` + `end_time` (auto-computed); times parsed from natural formats ("9am", "14:30", "2pm")
  - Backed by `sp_time_logs` table in postgres-brain; hours stored as `DECIMAL(5,2)`
  - Signal pattern `_SIGNAL_HOURS` triggers auto tool-forcing for messages like "I worked 3 hours" or "started at 9am ended at 2pm"
- **`sp_recipes`** — `SummitPineRecipesSkill` — `add`, `get`, `list`, `update`, `delete`
  - Recipes stored as JSONB ingredients array `[{name, amount, unit}]`; tags support multi-filter browsing
  - Previously the `recipes` table had no grants; Phase 7 added GRANT + RLS policy for `sp_app`
- **`sp_promotions`** — `SummitPinePromotionsSkill` — `create`, `list`, `get`, `update`, `deactivate`
  - `sp_promotions` table: code (UNIQUE), discount_type (percent/fixed/bogo), applies_to (all/sku/category), sku_list (TEXT[]), max_uses, uses_count
  - Signal pattern `_SIGNAL_PROMOTIONS` auto-routes promotion requests

#### Chunk 7B: Open Brain MCP — New Tool Modules

- **`tools/time_log.py`** — REST handlers for `log_hours`, `list_time_logs`, `time_summary`; `_parse_time()` helper for string→`time` conversion; hours auto-computed from start+end when not provided
- **`tools/recipes.py`** — REST handlers for recipe CRUD; ingredients stored as JSONB
- **`tools/promotions.py`** — REST handlers for promotion lifecycle; `increment_uses()` for coupon redemption tracking
- **`main.py`** — 15 new endpoints added: `/tools/log_hours`, `/tools/list_time_logs`, `/tools/time_summary`, `/tools/add_recipe`, `/tools/get_recipe/{id}`, `/tools/list_recipes`, `/tools/update_recipe/{id}`, `/tools/delete_recipe/{id}`, `/tools/create_promotion`, `/tools/list_promotions`, `/tools/get_promotion/{id}`, `/tools/update_promotion/{id}`, `/tools/deactivate_promotion/{id}`, `/tools/increment_promotion_uses/{id}`

#### Chunk 7C: Database — New Tables & Grants

```sql
-- New tables in postgres-brain
sp_time_logs   — labour hour records with optional start/end times and hourly rate
sp_promotions  — discount codes with type, scope, usage limits, and date windows
recipes        — production recipes (already existed; grants added)
```

- RLS enabled on all three tables; `sp_app` role granted SELECT/INSERT/UPDATE
- All tables have `created_at TIMESTAMPTZ DEFAULT NOW()` and UUID primary keys

#### Chunk 7D: Receipt & Note Ingestion

- **Telegram gateway**: hours-worked ack pattern (`_HOURS_PATTERN`) added to `handle_message()` — detects "worked X hours", "started at 9", "ended at 2", "log hours" and sends `⏱️ Got it, logging your hours...` ack
- **Dashboard Costs tab — Scan Receipt**: PDF branch added using `pypdf.PdfReader` to extract text; image branch (pytesseract OCR) unchanged; both paths forward extracted text to agent `/chat`
- **Dashboard Inventory tab — Quick Ingest**: text area + radio (Auto-detect / Inventory update / Expense log) generates a directed prompt to agent; handles bullet-point supply lists and free-form purchase notes

#### Chunk 7E: Streamlit Dashboard Expansion (10 tabs)

| Tab | Contents |
|---|---|
| Dashboard | KPIs, recent activity |
| Inventory | Stock table + Quick Ingest panel |
| Batches | Production batch records |
| Orders | Order history |
| Costs | Expense log + Scan Receipt (PDF + image) |
| **Hours** | Monthly summary metrics, time log table, Log Hours form |
| **Sales Analytics** | Revenue/Orders/AOV/Refunds KPIs, weekly stacked bar by channel, top products from JSONB, channel split pie |
| **Recipes** | Browse/filter by tag, expandable recipe cards, Add Recipe form |
| **Promotions** | Active promotions table, Create Promotion form, Deactivate form |
| FAQ | Product knowledge search |

- Dashboard calls `psycopg2` directly for tables/forms; calls agent `/chat` for AI-assisted ingestion
- `pypdf>=4.0.0` added to `summit-pine-ui/requirements.txt`

---

### PHASE 8: Integrations & Infrastructure (In Progress)

> Goal: Connect the agent to external productivity tools and expand infrastructure capabilities. Also includes infrastructure upgrades (monitoring, observability).

#### Chunk 8A: Monitoring Infrastructure ✅

**Status: COMPLETE**

Replaced the hand-rolled Streamlit health dashboard with industry-standard Prometheus + Grafana.

**What was implemented:**
- `agent-core/metrics.py` — Prometheus metric definitions: `agent_chat_requests_total` / `agent_chat_responses_total` (labels: channel, model), `agent_skill_calls_total` / `agent_skill_errors_total` (label: skill_name), `agent_policy_decisions_total` (labels: decision, zone), `agent_approval_events_total` (label: status), `agent_chat_response_ms` histogram (label: model, 8 buckets 250ms–60s), `agent_queue_depth` gauge, `agent_pending_approvals` gauge.
- `agent-core/tracing.py` — All `log_*` emitters now increment the corresponding Prometheus metric at emit time (zero code change required in calling code).
- `agent-core/app.py` — `GET /metrics` endpoint (no auth — internal network only) serves `generate_latest()`. `_update_gauges()` background asyncio task refreshes `queue_depth` and `pending_approvals` gauges from Redis every 15 seconds.
- `agent-core/requirements.txt` — `prometheus_client` added.
- `prometheus/prometheus.yml` — Scrape config: `agent-core:8000` and `ollama-runner:11434` (Ollama exposes native Prometheus metrics). 15s scrape interval, 30d TSDB retention.
- `grafana/provisioning/datasources/prometheus.yaml` — Auto-provisions Prometheus datasource at startup (no manual UI steps).
- `grafana/provisioning/dashboards/provider.yaml` + `agent.json` — Pre-built 12-panel "Agent Health" dashboard, loaded automatically. Panels: request rate, queue depth, pending approvals, skill calls (1h), policy denials (1h), Ollama pending requests, chat requests by channel (time series), response time p50/p95/p99 by model, skill calls by name, policy decisions by type, Ollama VRAM allocated, Ollama request duration p95.
- `docker-compose.yml` — `prometheus` and `grafana` services added with `prometheus_data` and `grafana_data` named volumes. Grafana on host port 3000.
- `.env.example` — `GRAFANA_ADMIN_PASSWORD` added.

**Key architecture decision:** The old Streamlit dashboard (`dashboard/`) is retained for Redis log browsing (raw event feed, security/audit panel, pending approvals list). Grafana handles all time-series operational monitoring. The two complement each other.

#### Post-8A Patch: Model Upgrade — qwen3:8b → gemma4:e4b ✅

**Status: COMPLETE**

Replaced qwen3:8b with Google's Gemma 4 E4B as the REASONING_MODEL, TOOL_MODEL, and CODING_MODEL across the entire stack.

**Motivation:**
- qwen3:8b required `think=False` to prevent it reasoning itself out of tool calls, plus 15+ regex signal patterns to force tool use — maintenance burden that grows with every new skill
- gemma4:e4b has native, architecture-level function calling support (τ2-bench Retail: 86.4% vs Gemma 3's 6.6%) — eliminates the need for tool-forcing hacks
- 128K context window vs qwen3:8b's ~32K — handles long conversation histories and document-heavy tasks without truncation

**Hardware fit:** 4.5B effective params (26B MoE), ~6 GB VRAM minimum — fits cleanly within the GTX 1070's 8 GB with 2 GB headroom. Ollama must be updated to v0.18+ (gemma4 requires a newer model format than v0.17).

**Files changed:**
- `docker-compose.yml` — `REASONING_MODEL`, `TOOL_MODEL`, `CODING_MODEL`, `LLM_MODEL` → `gemma4:e4b`
- `agent-core/app.py` — hardcoded defaults for all three model constants → `gemma4:e4b`
- `open-brain-mcp/metadata.py` — `REASONING_MODEL` default → `gemma4:e4b`
- `web-ui/app.py` — routing hint help text updated
- `agent-core/tests/test_skills.py` — `PythonExecSkill` test fixture model → `gemma4:e4b`

**Pull command:** `docker exec ollama-runner ollama pull gemma4:e4b` (requires Ollama updated first: `docker compose pull ollama-runner && docker compose up -d ollama-runner`)

**Full details:** See `SETUP_GUIDE_10.md` and `VIDEO_OUTLINE_11.md`.

#### Chunk 8B: Notion Integration (or similar)

- Read/write pages, databases, and tasks in Notion (or an alternative like Obsidian, Logseq)
- Skill wrapping with full policy enforcement
- Details TBD when we get here

#### Chunk 8C: Docker Management

- **Approach TBD — this needs to be considered very carefully.**
- The agent managing its own Docker infrastructure is inherently risky (container escape, self-modification)
- If implemented: read-only operations first (inspect, logs, stats), write operations (restart, scale) only with approval
- May decide not to implement this at all

---

### NOT FOR NOW

The following capabilities are explicitly deferred:

| Capability | Reason |
|---|---|
| **Slack/Discord integration** | No current need. Can be added later as thin gateway adapters (same pattern as telegram-gateway). |
| **Email** | High risk surface (sending email as owner, phishing vectors). May revisit later but staying away for now. |

---

## 7. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| LLM Runtime | Ollama | latest | Local model inference |
| Default Model | Phi-4 Mini | phi4-mini:latest | 3.8B params, fast tasks |
| Reasoning / Tool / Coding Model | Gemma 4 | gemma4:e4b | 4.5B effective params (26B MoE), native function calling, 128K context, ~6 GB VRAM on GTX 1070 |
| Deep Model | Qwen 2.5 | qwen2.5:14b | 14B params, long-context deep tasks (32K ctx) |
| Embedding Model | nomic-embed-text | (via Ollama) | Served by ollama-runner; used by all ChromaDB paths via OllamaEmbeddingFunction |
| Agent API | FastAPI | 0.115.0 | Central /chat endpoint |
| ASGI Server | Uvicorn | 0.32.0 | Serves FastAPI |
| Ollama Client | ollama-python | 0.3.3 | Python client for Ollama API |
| CLI Framework | Click | 8.1.7 | Command-line interface |
| Telegram Bot | python-telegram-bot | 21.5 | Telegram gateway |
| Web UI | Streamlit | latest | Browser-based chat interface |
| LLM Orchestration | LangChain | latest | Used in web UI for ChatOllama, embeddings, text splitting |
| Vector DB | ChromaDB | latest | RAG document storage, chat persistence |
| Embeddings Model | nomic-embed-text | (via Ollama) | Used by all ChromaDB paths (rag_ingest, rag_search, memory.py, web-ui) via OllamaEmbeddingFunction |
| Cache/Memory | Redis | alpine | Conversation history, job queue, chat queues, approval pub/sub |
| Metrics Instrumentation | prometheus_client | latest | Exposes Prometheus-format counters/histograms/gauges from agent-core at `/metrics` |
| Metrics Store | Prometheus | latest | Scrapes agent-core + Ollama every 15s; TSDB with 30-day retention |
| Operational Dashboard | Grafana | latest | 12-panel "Agent Health" dashboard; auto-provisioned from `grafana/provisioning/`; port 3000 |
| Personal Memory DB | PostgreSQL + pgvector | pg16 | Semantic memory store for Open Brain MCP (768-dim vectors, HNSW index) |
| Personal Memory API | Open Brain MCP | (custom) | FastAPI memory service: thoughts, household data, identity ingest, MCP SSE endpoint |
| STT | faster-whisper | latest | Speech-to-text in mumble-bot (small model, CPU, int8 quantization) |
| TTS | Piper | en_US-lessac-medium | Text-to-speech in mumble-bot (ONNX, CPU, local voice) |
| Voice Activity | webrtcvad | latest | VAD frame buffering and silence detection in mumble-bot |
| Mumble Protocol | pymumble_py3 | latest | Mumble client protocol in mumble-bot (UserState cert hash access) |
| Mumble Server | Murmur | mumblevoip/mumble-server:latest | Self-hosted Mumble voice server |
| Calendar Auth | MSAL (microsoft-authentication-library) | latest | Device code flow for MS Graph OAuth2 token cache |
| CalDAV | vobject / caldav | latest | Proton Calendar CalDAV access |
| Container Runtime | Docker Compose | 3.8 | Service orchestration |
| Language | Python | 3.12 | All custom services |

---

## 8. Environment Variables

All secrets are stored in `.env` in the project root. **Never commit this file.**

**IMPORTANT: The LLM must never see raw secret values.** Secrets are injected into skill execution at runtime via a secret broker, outside the LLM context window. See Chunk 4A (Secret Broker module).

| Variable | Used By | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | telegram-gateway | Bot token from @BotFather |
| `CHAT_ID` | telegram-gateway | Your numeric Telegram chat ID (for filtering) |
| `AGENT_URL` | telegram-gateway | URL to reach agent-core (`http://agent-core:8000`) |
| `REDIS_PASSWORD` | redis, agent-core, telegram-gateway, dashboard | Redis server password. Used in `--requirepass` on the redis container and embedded in `REDIS_URL` for all clients. |
| `REDIS_URL` | agent-core, telegram-gateway, dashboard | Redis connection string including password (`redis://:${REDIS_PASSWORD}@redis:6379`) |
| `AGENT_API_KEY` | agent-core, telegram-gateway, web-ui | Shared API key required in the `X-Api-Key` header for `POST /chat` and `POST /approval/{id}/respond`. Generated with `secrets.token_urlsafe(32)`. |
| `DEFAULT_MODEL` | agent-core | Default Ollama model for fast tasks (default `phi4-mini:latest`) |
| `REASONING_MODEL` | agent-core | Stronger Ollama model for planning/reasoning (default `gemma4:e4b`) |
| `CODING_MODEL` | agent-core | Model used for coding tasks when coding keywords detected (default `gemma4:e4b`). Use model alias `"code"` to force. |
| `BOOTSTRAP_MODEL` | agent-core | Model used during bootstrap conversation (default `mistral:latest`) |
| `DEEP_MODEL` | agent-core | Large-context model for complex tasks (default `qwen2.5:14b`) |
| `DEEP_NUM_CTX` | agent-core | Context window size for deep/coding models (default `32768`) |
| `NUM_CTX` | agent-core | Context window size for standard models (default `32768`) |
| `HISTORY_TOKEN_BUDGET` | agent-core | Max tokens for conversation history truncation (default `6000`) |
| `TOOL_MODEL` | agent-core | Model used for tool calling when skills are registered (default `gemma4:e4b`). Overrides auto-routing for `model=null` non-coding requests. |
| `EMBED_MODEL` | agent-core, web-ui | Ollama model for embeddings via OllamaEmbeddingFunction (default `nomic-embed-text`). Must be pulled. |
| `OLLAMA_HOST` | agent-core, web-ui | Ollama HTTP endpoint (default `http://ollama-runner:11434`). Used by OllamaEmbeddingFunction and heartbeat version check. |
| `WATCH_MODEL` | agent-core (heartbeat) | Model tag to mention in Ollama update notifications (default `qwen3.5:35b-a3b`). Not pulled automatically. |
| `MAX_TOOL_ITERATIONS` | agent-core | Hard cap on tool-call rounds per request before forcing a final answer (default `5`) |
| `BRAVE_SEARCH_API_KEY` | secret broker → web_search skill | API key for Brave Search (primary backend). Get a key at brave.com/search/api. Free tier includes $5/month credit (~1000 queries). Set in `.env`; injected via docker-compose. Never passed to the LLM. |
| `TAVILY_API_KEY` | secret broker → web_search skill | API key for Tavily web search (fallback when Brave fails). Get a free key at tavily.com. Set in `.env`; injected into agent-core via docker-compose. Never passed to the LLM. |
| `HEARTBEAT_INTERVAL_SECONDS` | agent-core | Seconds between heartbeat ticks (default `60`). Set to `0` to disable. |
| `BRAIN_URL` | agent-core, mumble-bot | URL for Open Brain MCP service (`http://open-brain-mcp:8002`). Used for brain context injection and "save that" voice capture. |
| `BRAIN_POSTGRES_PASSWORD` | postgres-brain, open-brain-mcp | PostgreSQL password for the `brain` database. Generate with `secrets.token_urlsafe(32)`. |
| `MS_GRAPH_CLIENT_ID` | agent-core (calendar_auth), open-brain-mcp | Azure app client ID for MSAL device code flow. Register app in Azure portal (public client, Calendars.Read + Calendars.ReadWrite scopes). |
| `MUMBLE_SUPERUSER_PASSWORD` | mumble-server | Murmur admin (SuperUser) password. Set before first boot — used to configure channels. |
| `MUMBLE_SERVER_PASSWORD` | mumble-bot | Optional: Mumble server join password (leave blank for open access within the network). |
| `MUMBLE_OWNER_USERNAMES` | mumble-bot | Comma-separated list of Mumble display names trusted as owner (fallback auth before cert hash is set). Default: `Andy`. |
| `MUMBLE_OWNER_CERT_HASH` | mumble-bot | Certificate fingerprint for the owner's Mumble client (primary, cryptographic auth). Discovered from bot logs on first connect. Leave blank until obtained. |
| `PROTON_CALDAV_URL` | agent-core (calendar_read/write) | CalDAV endpoint for Proton Calendar (via proton-bridge container). |
| `PROTON_CALDAV_USER` | agent-core (calendar_read/write) | Proton account username for CalDAV auth. |
| `PROTON_CALDAV_PASSWORD` | agent-core (calendar_read/write) | Proton bridge-specific password (not account password — generated by proton-bridge CLI). |

---

## 9. How to Use This Document

This PRD is designed so that an AI chat session can pick up any chunk of work cold. To start a new work session:

1. **Give the AI this entire document** as context
2. **Specify which chunk** you want to work on (e.g., "Implement Chunk 2A: Soul File")
3. **Point it at the relevant files** — the file tree and service descriptions tell it exactly what exists
4. **The known issues section** tells it what's broken before it starts
5. **The security philosophy section** is mandatory reading — every implementation must respect it

Each chunk is scoped to be completable in a single focused session. Chunks within a phase can generally be done in any order, but some chunks have explicit prerequisites:
- **Chunk 3A (Policy Engine) must be built before Chunk 2A (Soul/Bootstrap)** — the bootstrap process is the first consumer of the policy engine's approval gates. ✅ Both done.
- **Chunk 4A (Skill Framework) must be built before any other 4x chunk** — all skills depend on the framework.
- **Chunk 4C (Memory) requires a sanitization layer** before going live — web content can poison memory via hidden instructions.
- Phases are otherwise sequential (Phase 3 before Phase 4, etc.).

When a chunk is completed, update this document:
- Move the chunk status from ⬜ to ✅
- Update the "Current State" section for any modified services
- Add any new known issues discovered during implementation
- Update the file tree if new files were added
