# My-Agent: Product Requirements Document

> **Last Updated:** 2026-02-16
> **Owner:** Andy
> **Status:** Active development — Phase 1 complete, Phase 2 complete (all chunks: 2A, 2B, 2C, 2D done). Phase 3 partially complete (3A done; 3B, 3C not started). Next up: Chunk 3B (Observability), then Phase 4 (Skill Framework + Skills).

---

## 1. Project Overview

**My-Agent** is a self-hosted, multi-interface AI agent stack running entirely on local hardware via Docker. It wraps locally-hosted LLMs (Ollama with Phi-3 Mini for fast tasks and Llama 3.1 8B for reasoning) behind a central FastAPI service, with multiple frontends (CLI, Telegram bot, Streamlit web UI) and optional RAG via ChromaDB.

The project is inspired by the Openclaw (formerly Moltbot/Clawdbot) approach: a local-first, action-oriented AI agent that runs on your own machine, connects to your chat apps, and can eventually execute real tasks with persistent memory.

### Design Principles

- **One brain, many interfaces** - All LLM logic lives in agent-core; frontends are thin adapters
- **Local-first** - Everything runs on your hardware, no cloud API dependencies
- **Containerized** - Each service is isolated in Docker, communicating over a private bridge network
- **Incremental** - Built one capability layer at a time, from basic chat up to autonomous agent

### Target Environment

- Linux (primary), Mac, or Windows (WSL2)
- CPU-only (no GPU required)
- 8+ GB RAM recommended (4 GB minimum for phi3)
- Docker and Docker Compose

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        agent_net (Docker bridge)                │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ ollama-runner │    │  chroma-rag  │    │    redis     │       │
│  │ (LLM engine) │    │  (vector DB) │    │  (active)    │       │
│  │  :11434 int   │    │ :8000 int    │    │  :6379 int   │       │
│  │  no host port │    │ :8100 host   │    │  no host port│       │
│  └──────┬───────┘    └──────┬───────┘    └──────────────┘       │
│         │                   │                                   │
│         │ Ollama API        │ ChromaDB API                      │
│         │                   │                                   │
│  ┌──────┴───────────────────┴───────┐                           │
│  │          agent-core              │                           │
│  │     (FastAPI - central hub)      │                           │
│  │       :8000 int & host           │                           │
│  └──┬──────────────┬───────────┬────┘                           │
│     │              │           │                                │
│     │ /chat        │ /chat     │ /chat                          │
│     │              │           │                                │
│  ┌──┴─────┐  ┌─────┴────┐  ┌──┴──────────┐                     │
│  │telegram │  │  web-ui  │  │    CLI      │                     │
│  │-gateway │  │(Streamlit│  │(click, runs │                     │
│  │         │  │  :8501)  │  │ in-container│                     │
│  │no host  │  │host:8501 │  │ or host)    │                     │
│  │port     │  │          │  │             │                     │
│  └─────────┘  └──────────┘  └─────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### Service Map

| Service | Container Name | Image / Build | Internal Port | Host Port | Depends On |
|---|---|---|---|---|---|
| ollama-runner | ollama-runner | `ollama/ollama:latest` | 11434 | none | - |
| agent-core | agent-core | `./agent-core` (build) | 8000 | 8000 | ollama-runner (healthy), redis |
| telegram-gateway | telegram-gateway | `./telegram-gateway` (build) | - | none | agent-core (healthy), redis |
| chroma-rag | chroma-rag | `chromadb/chroma:latest` | 8000 | 8100 | - |
| web-ui | web-ui | `./web-ui` (build) | 8501 | 8501 | agent-core, chroma-rag |
| redis | redis | `redis:alpine` | 6379 | none | - |

### Volume Mounts

| Mount | Container Path | Purpose | Agent Access |
|---|---|---|---|
| Dedicated drive (host) | `/sandbox` | Agent's playground — experiments, scripts, scratch files, daily logs | Full read/write/delete |
| Named volume or host dir | `/agent` | Identity files — SOUL.md, IDENTITY.md, USER.md, MEMORY.md | Read freely, write only with owner approval |
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
  → agent-core checks for "search docs" keyword
    → YES: query ChromaDB, return documents
    → NO:
      → Load identity files from /agent (hot-reload on every request)
      → build_system_prompt(): bootstrap mode uses BOOTSTRAP.md, normal mode uses SOUL.md + AGENTS.md + USER.md
      → route_model() selects model:
        - model="deep" alias → DEEP_MODEL (qwen2.5:14b)
        - model="reasoning" alias → REASONING_MODEL (llama3.1:8b)
        - model=<specific> → use as-is (client override)
        - model=None → auto-route: check message for reasoning keywords
          → match → REASONING_MODEL (llama3.1:8b)
          → no match → DEFAULT_MODEL (phi3:latest)
      → Load conversation history from Redis (per user_id)
      → Truncate history to HISTORY_TOKEN_BUDGET (skipped during bootstrap)
      → Prepend system prompt + send history + new message to Ollama
      → If bootstrap mode: extract file proposals, validate, send through approval gate
      → Save updated history to Redis
  → Response JSON: { response: "...", model: "<model used>" }
← Frontend displays response to user
```

---

## 3. Current State of Each Service

### 3.1 ollama-runner

**Status: WORKING**

- Official `ollama/ollama:latest` Docker image
- Models:
  - `phi3:latest` (3.8B params, CPU-friendly) — default fast model
  - `llama3.1:8b` (8B params) — reasoning model for complex tasks
- Persistent volume `ollama_data` at `/root/.ollama`
- Healthcheck: `ollama list` every 30s
- No host port exposed (internal only via `agent_net`)
- **Note:** `llama3.1:8b` must be pulled manually: `docker exec ollama-runner ollama pull llama3.1:8b`

### 3.2 agent-core

**Status: WORKING (with policy engine, identity system & bootstrap)**

The central hub. FastAPI service that wraps Ollama, with policy engine, approval system, identity loader, and conversational bootstrap.

**Files:**

| File | Purpose |
|---|---|
| `app.py` | FastAPI service with `/chat`, `/health`, `/bootstrap/status`, `/chat/history/{user_id}`, `/policy/reload`, `/approval/*` endpoints. Integrates identity loading, bootstrap proposal handling, and approval gates. |
| `cli.py` | Click CLI with `chat` (supports `--model`, `--reason`/`-r`, `--session`) and `serve` commands |
| `tools.py` | Tool definitions (stub only, not wired in — sandbox paths updated to `/sandbox`) |
| `policy.yaml` | Zone rules, rate limits, approval settings, denied URL patterns (mounted read-only) |
| `policy.py` | Central policy engine: 4-zone model, hard-coded deny-list, rate limiting, access checks |
| `approval.py` | Approval gate manager: Redis hash storage, pub/sub notifications, async wait, timeout, proposed_content support |
| `approval_endpoints.py` | FastAPI router for approval inspection and resolution |
| `identity.py` | Identity file loader: reads SOUL.md, IDENTITY.md, USER.md, AGENTS.md, BOOTSTRAP.md from `/agent`. Builds composite system prompt. Detects bootstrap mode. Hot-reloads on every request. |
| `bootstrap.py` | Bootstrap proposal parser: extracts `<<PROPOSE:FILE.md>>` markers from LLM output, validates filenames and content, checks bootstrap completion, deletes BOOTSTRAP.md when done |
| `skill_contract.py` | Abstract `SkillBase` class defining the interface for all future skills |
| `agent` | Shell wrapper (`#!/bin/bash`) so `agent chat "msg"` works on PATH |
| `Dockerfile` | Python 3.12, installs deps, copies CLI to `/usr/local/bin/agent` |
| `requirements.txt` | fastapi, uvicorn, ollama, click, requests, chromadb, redis, pyyaml |
| `tests/` | Unit tests (policy, approval, identity, bootstrap), runnable without Docker |

**API Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Main chat endpoint. Accepts `ChatRequest` (message, model, user_id, channel, auto_approve). Loads identity, builds system prompt, routes to ChromaDB or Ollama, handles bootstrap proposals. Returns `{ response, model }`. |
| GET | `/health` | Returns `{"status": "healthy"}`. Used by Docker healthcheck and dependent services. |
| GET | `/bootstrap/status` | Returns `{"bootstrap": true/false}`. Checks if BOOTSTRAP.md exists. |
| GET | `/chat/history/{user_id}` | Retrieve conversation history for a session from Redis. |
| POST | `/policy/reload` | Hot-reload policy.yaml without container restart. |
| GET | `/approval/pending` | List all pending approval requests. |
| GET | `/approval/{id}` | Check a specific approval's status. |
| POST | `/approval/{id}/respond` | Resolve an approval (approve/deny). Called by telegram-gateway. |

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

**Model routing (`route_model()`):**
- `model="deep"` → resolves to `DEEP_MODEL` (qwen2.5:14b with 16K context)
- `model="reasoning"` → resolves to `REASONING_MODEL` (special alias)
- `model=<any other value>` → used as-is (client override)
- `model=null` (default) → auto-route based on message content: if any reasoning keyword is detected (`explain`, `analyze`, `plan`, `code`, `why`, `compare`, `debug`, `reason`, `think`, `step by step`, `how does`, `what if`), uses `REASONING_MODEL`; otherwise uses `DEFAULT_MODEL`

**Current limitations:**
- ~~**Stateless** - Every `/chat` call is independent. No conversation history.~~ FIXED (Chunk 2B): Redis-backed conversation memory with token-budget truncation.
- **No tool execution** - `tools.py` defines tools as a dict but nothing reads or executes them.
- ~~**Single model** - Always uses the model specified in the request (defaults to phi3). No routing logic.~~ FIXED (Chunk 2C): `route_model()` auto-routes between phi3 (fast) and llama3.1:8b (reasoning) based on message keywords, with client override and `--reason` flag support.
- **RAG routing is keyword-based** - Checks for literal string "search docs" in the message. Not intelligent routing.
- ~~**`requirements.txt` is missing `chromadb`** - Fixed: `chromadb` added to requirements.txt.~~

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
- **Boot greeting** via `post_init` - sends a time-aware greeting message when the stack comes up (now includes "Policy Engine: Guardrails active")
- **Chat ID filtering** - only responds to the owner's chat ID (set via `CHAT_ID` env var)
- **Auto-routing** - does not send a model to agent-core, allowing server-side auto-routing (simple messages use phi3, complex questions escalate to llama3.1:8b)
- **Typing indicator** - continuous "typing..." status while waiting for Ollama to respond
- **Message chunking** - splits long responses at line breaks/spaces to stay under Telegram's 4096 char limit
- **Approval inline keyboards** (Chunk 3A) - subscribes to Redis `approvals:pending` channel, shows Approve/Deny buttons with risk-level emoji, writes resolution back to Redis hash
- **Approval catch-up** - on startup, scans for any pending approvals missed during downtime and re-sends them
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
- Used by agent-core's `rag_tool()` function and by the web UI for embeddings + chat persistence

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

### 3.6 redis

**Status: WORKING**

- `redis:alpine` image in docker-compose.yml
- Connected to `agent_net`
- `restart: unless-stopped`
- Used by agent-core for conversation history storage (per user_id session keys)
- Used by agent-core + telegram-gateway for approval gate (hash storage + pub/sub)
- Intended future use: job queue, scheduled tasks, memory state

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
│   ├── app.py                  # FastAPI: /chat, /health, /bootstrap/status, /chat/history, /policy/reload, /approval/*
│   ├── cli.py                  # Click CLI: chat, serve commands
│   ├── tools.py                # Tool definitions (STUB - not wired in)
│   ├── policy.yaml             # Zone rules, rate limits, approval config (read-only mount)
│   ├── policy.py               # Central policy engine (zones, deny-list, rate limits)
│   ├── approval.py             # Approval gate manager (Redis hash + pub/sub + proposed_content)
│   ├── approval_endpoints.py   # REST router: /approval/pending, /{id}, /{id}/respond
│   ├── identity.py             # Identity file loader, system prompt builder, bootstrap detection
│   ├── bootstrap.py            # Bootstrap proposal parser, validator, completion checker
│   ├── skill_contract.py       # Abstract SkillBase class for all future skills
│   ├── agent                   # Shell wrapper for CLI on PATH
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py         # FakeRedis mock, policy_engine & approval_manager fixtures
│       ├── test_policy.py      # 51 tests: deny-list, zones, external access, rate limits
│       ├── test_approval.py    # 13 tests: create, resolve, timeout, get_pending
│       ├── test_identity.py    # Identity loader tests: bootstrap detection, file loading, prompt building
│       └── test_bootstrap.py   # Bootstrap parser tests: proposal extraction, validation, completion, approval integration
│
├── agent-identity/             # Bind-mounted to /agent in container (Zone 2)
│   ├── SOUL.md                 # Agent personality prompt (written during bootstrap)
│   ├── IDENTITY.md             # Structured fields: name, nature, vibe, emoji
│   ├── USER.md                 # Owner profile: name, preferences, timezone
│   └── AGENTS.md               # Operating instructions (static rules)
│
├── telegram-gateway/
│   ├── Dockerfile              # Python 3.12-slim
│   ├── requirements.txt        # python-telegram-bot, requests, redis
│   └── bot.py                  # Telegram bot: greeting, typing, chunking, approval callbacks
│
├── web-ui/
│   ├── Dockerfile              # Python 3.12-slim + system deps
│   ├── requirements.txt        # streamlit, langchain, chromadb
│   └── app.py                  # Streamlit chat UI with RAG
│
├── ollama/                     # Empty directory (placeholder)
│
├── SETUP_GUIDE.md              # Full setup walkthrough for new users (Phase 1 stack)
├── SETUP_GUIDE_2.md            # Policy engine, guardrails & identity bootstrap setup guide
├── VIDEO_OUTLINE.md            # YouTube video 1 outline (foundation stack)
├── VIDEO_OUTLINE_2.md          # YouTube video 2 outline (guardrails + identity/bootstrap)
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

---

## 6. Roadmap

Based on the Openclaw capability model, the project builds up in layers from "LLM in a loop" to a continuously running, tool-rich, local-first agent with its own memory, skills, and job system. Each phase is designed to be tackled as independent work chunks.

### Openclaw Alignment

The roadmap is designed to reach feature parity with Openclaw's core architecture while maintaining a stronger security posture.

| Openclaw Pillar | Our Approach | Phase |
|---|---|---|
| Long-lived agent on your own machine | Docker Compose, `restart: unless-stopped`, heartbeat loop | 1 (done) + 4C |
| Gateway architecture (one brain, many apps) | agent-core hub + thin adapter pattern | 1 (done) |
| Model-agnostic / brain-vs-muscle routing | Multi-model Ollama + keyword-based auto-routing (`route_model()`) | 2 (done) |
| Soul / Persona file | Conversational bootstrap (Openclaw-inspired) with policy-gated file writes. SOUL.md, IDENTITY.md, USER.md co-authored by agent + owner. | 2A (done) |
| Conversation memory | Redis rolling history per user/session | 2 (done) |
| Policy, guardrails, observability | Four-zone permission model, approval gates, rate limits, structured tracing, health dashboard. **Built before soul/bootstrap.** | 3A (done) |
| Modular skill system | Local `skills/` directory, hand-built or vetted, no external marketplaces. Each skill enforces its own security. | 4A |
| First skills (search, files, RAG) | Web search, URL fetch, file read/write, PDF parse, RAG retrieval. Secret broker for API keys. | 4B |
| Memory & scheduled tasks | Persistent memory with sanitization layer, heartbeat/cron, task management. | 4C |
| Full system access (files, shell, APIs) | Four-zone model: `/sandbox` (free), `/agent` (approval), system (never), external (explore free, act with approval). Docker isolation + policy engine. | 4B-4F |
| Credential security | Secret broker pattern — LLM never sees raw credentials | 4B |
| Heartbeat / observe-reason-act loop | Background event loop in agent-core that checks triggers | 4C |
| Jobs & automations system | Redis-backed task queue with scheduled + event triggers | 4C |
| Persistent memory (notes, tasks, results) | Multi-layer: Redis (short-term) + ChromaDB (long-term) with sanitization | 4C |
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
- `agent-core/app.py` — Integrated identity and bootstrap: loads identity on each `/chat` request, builds composite system prompt, detects bootstrap mode, extracts proposals from LLM response, sends each through approval gate via `handle_bootstrap_proposal()`, supports `auto_approve` flag for testing. Added `/bootstrap/status` and `/chat/history/{user_id}` endpoints. During bootstrap, history truncation is skipped to preserve full conversation context.
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
2. Agent initiates a natural conversation (not an interrogation):
   - "Hey. I just came online. Who am I? Who are you?"
   - Together, figure out: agent's name, nature, vibe, emoji
   - Discuss: what matters to the owner, how the agent should behave, boundaries
3. Agent **proposes** content for each file using `<<PROPOSE:FILE.md>>` markers
4. agent-core extracts proposals, validates, and sends through approval gate (Telegram inline keyboards)
5. Only after owner approval does the agent write to `IDENTITY.md`, `USER.md`, and `SOUL.md`
6. `check_bootstrap_complete()` detects all three files exist → deletes `BOOTSTRAP.md`
7. On all subsequent sessions, `BOOTSTRAP.md` is absent, so the agent boots normally

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
- Bootstrap channel: Telegram first (primary interaction surface), web UI support later
- Template content: Openclaw-inspired defaults, iterated after first bootstrap
- Proposal format: `<<PROPOSE:FILENAME.md>>` markers parsed by regex
- Per-agent or global: Global for now (single agent), per-agent when multi-agent is added (Phase 3E)

---

#### Chunk 2B: Conversation Memory (Redis)

**Priority: HIGH — The single biggest functional improvement.**

Currently every `/chat` call is stateless. The agent has no idea what you said 10 seconds ago. This chunk wires Redis into agent-core to maintain rolling conversation history.

**Scope:**
- Add `redis` pip package to `agent-core/requirements.txt`
- Add `networks: [agent_net]` to redis service in `docker-compose.yml`
- In `agent-core/app.py`:
  - Connect to Redis at `redis:6379`
  - On each `/chat` request, load message history for the `user_id` (or generate a session ID if none provided)
  - Prepend the soul/system prompt
  - Append the new user message to history
  - Send the full message list (system prompt + history + new message) to Ollama
  - Append the assistant response to history
  - Implement a truncation strategy (e.g., keep last N messages, or trim to fit context window)
  - Add a TTL or max-length so sessions don't grow forever
- Update `ChatRequest` to make `user_id` more prominent (maybe default to channel + chat_id)
- Telegram gateway already sends `user_id` and `channel` — no changes needed there
- CLI should pass a `user_id` (e.g., `"cli-default"`) and optionally a `--session` flag

**Key decisions:**
- Message history format in Redis (list of JSON objects? single JSON blob?)
- Max history length (token-based or message-count-based?)
- Session TTL (expire after N hours of inactivity?)

**Test criteria:**
- Send "My name is Andy" via CLI, then send "What is my name?" — agent should remember
- Same test via Telegram
- Restart agent-core container — history should persist (Redis has its own persistence)

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

#### Chunk 3A: Policy Engine & Guardrails ✅

**Status: COMPLETE**

The policy engine enforces the four-zone permission model. Every action the agent takes — file writes, shell commands, API calls, identity file edits, external interactions — is checked against this engine before execution.

**What was built:**
- `agent-core/policy.yaml` — Zone rules, rate limits, approval config, denied URL patterns. Mounted read-only into the container.
- `agent-core/policy.py` — Central policy engine (~280 lines): `PolicyEngine` class with `resolve_zone()` (symlink-escape-safe via `os.path.realpath()`), `check_file_access()`, `check_shell_command()`, `check_http_access()`, `check_rate_limit()` (in-memory sliding window). Enums: `Zone`, `ActionType`, `Decision`, `RiskLevel`. Hard-coded `HARD_DENY_PATTERNS` as module-level Python constants (NOT from YAML — agent cannot weaken them).
- `agent-core/skill_contract.py` — Abstract `SkillBase` class with `SkillMetadata` dataclass. Interface for all future skills: `validate()`, `execute()`, `sanitize_output()`.
- `agent-core/approval.py` — `ApprovalManager` class: Redis hash storage at `approval:{uuid}`, pub/sub on `approvals:pending` channel, 5-minute auto-deny timeout, double-resolve protection, startup catch-up.
- `agent-core/approval_endpoints.py` — REST router: `GET /approval/pending`, `GET /approval/{id}`, `POST /approval/{id}/respond`.
- `agent-core/tests/` — 64 unit tests (51 policy + 13 approval), all passing, no Docker needed. Covers: deny-list patterns, zone enforcement, symlink escape, external access, rate limiting, approval lifecycle, timeout.
- `telegram-gateway/bot.py` — Updated with Redis subscription, InlineKeyboardMarkup for Approve/Deny, callback handler, startup catch-up for missed approvals.
- `docker-compose.yml` — Volumes: `agent_sandbox:/sandbox`, `agent_identity:/agent`, `policy.yaml:ro`. telegram-gateway now depends on Redis.

**Full details:** See `SETUP_GUIDE_2.md` and `VIDEO_OUTLINE_2.md`.

---

#### Chunk 3B: Observability & Structured Tracing

**Priority: HIGH**

**Scope:**
- Implement structured JSON logging for all agent activity in `agent-core/tracing.py`:
  - Every `/chat` request: timestamp, user_id, channel, message (truncated), model used
  - Every skill call: skill name, parameters (sanitized), result (truncated), duration_ms, success/failure
  - Every policy decision: what was checked, what was allowed/denied, why
  - Every approval gate event: requested, approved/denied, by whom, response time
- Use Python `logging` with JSON formatter — no heavy dependencies needed initially
- Log to stdout (Docker captures it) AND to a Redis list for the dashboard to read
- Per-request trace IDs so you can follow a single user message through the entire skill chain
- Cost/latency metrics per model (tokens in, tokens out, time to first token, total time)

**Test criteria:**
- A `/chat` request produces a structured JSON log line with all required fields
- Skill calls within a request share the same trace ID
- Logs are queryable from Redis by the dashboard

---

#### Chunk 3C: Health Dashboard

**Priority: HIGH — Gives the owner real-time visibility into what the agent is doing.**

A dedicated dashboard page (Streamlit or lightweight web app) that shows the operational state of the entire agent stack at a glance.

**Scope:**
- New service: `dashboard/` (Streamlit app on port 8502, or a new page in the existing web-ui)
- **System Health Panel:**
  - Live status of each service (agent-core, ollama-runner, chroma-rag, redis, telegram-gateway, web-ui) — green/yellow/red based on healthcheck
  - Container uptime, restart count
  - Ollama model(s) loaded, memory usage
  - Redis connection status, memory usage
  - ChromaDB collection count, document count
- **Activity Panel:**
  - Total requests today / this hour / this session
  - Requests by channel (Telegram, CLI, Web UI) — bar chart or counters
  - Skill execution counts by skill name — how many times each skill was called
  - Success/failure rates per skill
  - Average response time per model
- **Queue & Jobs Panel** (ready for Phase 5, shows empty until then):
  - Pending items in the job queue
  - Running jobs with progress/status
  - Recently completed jobs with results
  - Scheduled jobs and next fire time
- **Recent Activity Feed:**
  - Live tail of recent agent actions (last 50-100 entries)
  - Each entry: timestamp, channel, user, action taken, skill(s) called, duration
  - Filterable by channel, skill, time range
- **Security & Audit Panel:**
  - Denied actions (policy rejections, deny-list hits)
  - Approval gate history (requested, approved, denied)
  - Rate limit events
  - Any errors or anomalies

**Key decisions:**
- Separate Streamlit app vs. new tab in existing web-ui (separate is cleaner, avoids bloating the chat UI)
- Data retention period for dashboard metrics (24 hours? 7 days?)
- Authentication for the dashboard (initially none — it's on localhost only)

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

#### Chunk 4A: Skill Framework

**Priority: HIGH — Must be built first. All skills depend on this.**

Turn `tools.py` from a stub into a modular, locally-managed skill system.

**Scope:**
- Evolve `skill_contract.py` (from Chunk 3A) into a full `agent-core/skills/base.py` implementing the Security Contract:
  - `name`, `description` (for the LLM to understand what it does)
  - `parameters` (JSON Schema)
  - `risk_level` — "low", "medium", or "high"
  - `rate_limit` — max calls per time window
  - `requires_approval: bool` flag (derived from risk_level, overridable in policy.yaml)
  - `validate(params) -> bool` — skill-specific input validation (e.g., file paths checked against zone rules, shell commands checked against deny-list)
  - `execute(params) -> result` handler function
  - `sanitize_output(result) -> result` — strip secrets, truncate large outputs, neutralize prompt injection in tool results
- Every skill call passes through the policy engine (Phase 3A) before execution:
  1. Policy engine checks rate limits
  2. Policy engine checks approval requirements
  3. Skill's own `validate()` runs
  4. If all pass, `execute()` runs
  5. `sanitize_output()` cleans the result before it enters the LLM context
  6. Tracing (Phase 3B) logs the entire chain
- Implement a skill registry that loads skills from the local `skills/` directory at startup
  - **No remote skill fetching, no auto-discovery from external sources**
  - Adding a new skill = drop a Python file in `skills/` and restart agent-core
- Implement the tool execution loop in agent-core:
  1. Send user message + soul prompt + conversation history + available skills to Ollama
  2. If Ollama responds with a skill call, run it through the policy → validate → execute → sanitize pipeline
  3. Feed the sanitized skill result back to Ollama
  4. Repeat until Ollama responds with a final text answer or hits max iterations (from policy)
- Wire the RAG tool as the first skill (replace the hardcoded "search docs" keyword check)
- Ollama supports tool calling with compatible models (may need to switch to Llama 3.1+ or Mistral for good tool-use support)
- **Secret broker module** — skills call `secret_broker.get("TAVILY_API_KEY")` at execution time. The LLM context window NEVER contains raw credentials — only the query and sanitized results. Secrets stored in Docker secrets (preferred) or `.env` (development), never in agent-accessible config.

**Key decisions:**
- Which model to use for tool calling (phi3 may not support it well — this may force the brain-vs-muscle split)
- Max tool call iterations (prevent infinite loops — configured in policy, e.g., 5)
- Error handling when skills fail
- Approval gate UX: how does the agent ask for confirmation? (Telegram message? Web UI prompt?)

---

#### Chunk 4B: First Skills — Search, Files & RAG

**Priority: HIGH — The first real capabilities.**

Six skills that give the agent the ability to search the web, fetch URLs, read/write files, parse PDFs, and query the vector database. All read-heavy, low blast radius.

| Skill | Description | Risk Level | Approval | Key Security |
|---|---|---|---|---|
| `web_search` | Search the web via Tavily API (or similar) | Low | No | API key via secret broker, result sanitization, rate limited (10/min) |
| `url_fetch` | Fetch and extract content from a specific URL | Low | No | SSRF prevention (block internal IPs/Docker network), denied URL patterns from policy.yaml, response size limit, content sanitization |
| `file_read` | Read file contents | Low (sandbox), Medium (identity) | No (sandbox), No (identity read) | Path validation via `resolve_zone()`, no symlink escape, Zone 3+ denied |
| `file_write` | Write/create files | Low (sandbox), High (identity) | No (sandbox), Yes (identity) | Path validation, zone enforcement, identity writes require owner approval |
| `pdf_parse` | Extract text from PDF files | Low | No | Parse in sandbox only, size limits, output sanitization |
| `rag_search` | Query ChromaDB vector database for relevant documents | Low | No | Replaces hardcoded "search docs" keyword check, result truncation |

**Per-skill security details:**
- **web_search**: Results are sanitized before entering LLM context — strip HTML, remove hidden text, truncate to max chars. Treats all web content as potentially adversarial (hidden prompt injection in page content).
- **url_fetch**: Validates URL against denied patterns (paypal, stripe, billing, signup, register from policy.yaml). Blocks internal network addresses (10.x, 172.16-31.x, 192.168.x, localhost, Docker service names). Response body truncated and sanitized.
- **file_read/file_write**: `validate()` resolves the real path via `os.path.realpath()` and checks against zone rules. `../` traversal and symlink escape are caught. Identity file writes go through the approval gate.
- **pdf_parse**: Only operates on files in `/sandbox`. Uses a pure-Python PDF library (no shell calls). Output truncated to prevent context bloat.
- **rag_search**: Queries the existing ChromaDB instance. Results are truncated and returned as structured context.

**Test criteria:**
- Web search returns results for a query, API key is not in the LLM context
- URL fetch blocks internal network addresses (SSRF) and denied URL patterns
- File write to `/sandbox/test.txt` succeeds without approval
- File write to `/agent/SOUL.md` triggers approval gate
- File write to `/app/anything` is denied outright
- PDF parse extracts text from a test PDF in `/sandbox`
- RAG search returns relevant documents and replaces the keyword-based routing

---

#### Chunk 4C: Memory, Scheduled Tasks & Heartbeat

**Priority: HIGH — This is what turns a chatbot into an agent.**

Persistent memory with a sanitization layer, heartbeat/cron infrastructure, and task management. The memory system must sanitize all inputs to prevent poisoning (e.g., web content with hidden instructions writing to agent memory, compromising all future conversations).

**Memory/State Storage:**
- Multi-layer memory: Redis (fast short-term, session state) + ChromaDB (semantic long-term, user profile, notes)
- `remember` skill — agent writes facts/observations to memory
- `recall` skill — agent queries memory by semantic similarity or structured key
- **Memory sanitization layer** — all content written to memory is sanitized:
  - Strip hidden instructions, HTML tags, control characters
  - Detect and flag potential prompt injection patterns before storage
  - Separate "agent-generated" from "external-sourced" memory entries (web content flagged differently than owner-confirmed facts)
  - Rate limit memory writes to prevent flooding
- Long-term user profile: preferences, identity, recurring contexts (ChromaDB collection)
- Notes, tasks, and results store (Mission Control): structured Redis hashes + ChromaDB for search

**Heartbeat/Cron Infrastructure:**
- Background task in agent-core (FastAPI `on_startup`) that runs every N seconds (configurable via `HEARTBEAT_INTERVAL`)
- On each tick: check the job queue (Redis) for due tasks, evaluate triggers, execute via the standard skill pipeline
- Redis-backed job queue with three trigger types:
  - **Scheduled**: cron-style recurring tasks ("every morning at 9am, summarize my repos")
  - **Event-driven**: fire when a condition is met ("when a new GitHub issue is opened, triage it")
  - **One-shot**: deferred tasks ("remind me about X in 2 hours")
- Prevent overlapping executions (Redis lock)

**Task Management Skills:**
- `create_task` — create a scheduled, event-driven, or one-shot task (requires approval for recurring tasks)
- `list_tasks` — show pending/active/completed tasks
- `cancel_task` — cancel a scheduled task (requires approval)
- New API endpoints: `POST /jobs`, `GET /jobs`, `DELETE /jobs/{id}`
- Task persistence — survives container restarts via Redis

**Key decisions:**
- Heartbeat runs inside agent-core vs. a separate scheduler container
- Memory sanitization strictness (aggressive vs. permissive flagging)
- How to distinguish trusted (owner-confirmed) vs. untrusted (web-sourced) memory entries
- Default heartbeat interval (60s? 300s?)

**Test criteria:**
- Agent can `remember` a fact and `recall` it in a later conversation
- Memory sanitization strips hidden instructions from web-sourced content
- Scheduled task fires without user input
- Agent can create its own follow-up tasks during a conversation
- Tasks persist across container restarts
- Rate limiting prevents memory flooding

---

#### Chunk 4D: Math, Physics & Media

**Priority: MEDIUM**

| Skill | Description | Risk Level | Notes |
|---|---|---|---|
| `calculator` | Evaluate mathematical expressions safely | Low | Pure Python `eval()` alternative (e.g., `ast.literal_eval` or `sympy`). No shell. No arbitrary code execution. |
| `physics` | Unit conversions + physics knowledge | Low | Pure Python unit conversion library (e.g., `pint`). Optionally integrate Wolfram Alpha API for knowledge queries (API key via secret broker). No physics simulation — just conversions and factual knowledge. |
| `image_gen` | Generate images from text prompts | Medium | **ON HOLD** until GTX 1070 8GB GDDR5 arrives and is installed. Will likely use Stable Diffusion via a local inference container. Output saved to `/sandbox`. |

**Test criteria:**
- Calculator correctly evaluates expressions without arbitrary code execution
- Physics skill converts units accurately (e.g., "5 miles to km", "100 Fahrenheit to Celsius")
- Physics knowledge queries return accurate factual answers

---

#### Chunk 4E: Execution & Voice

**Priority: MEDIUM**

| Skill | Description | Risk Level | Approval | Notes |
|---|---|---|---|---|
| `python_exec` | Execute Python code in a sandboxed environment | High | Yes (always) | Runs in an isolated subprocess with restricted imports. Output captured and sanitized. No network access from sandbox. No file access outside `/sandbox`. |
| `calendar` | Interact with calendar (read events, create reminders) | Medium | Create: Yes | Details TBD — need to choose calendar backend (Google Calendar API? CalDAV? Local?) |
| `text_to_speech` | Convert text to audio | Low | No | Piper TTS (local, no API). Output saved to `/sandbox`. Used by Mumble gateway. |

**Mumble Voice Interface** — a new gateway container, same pattern as telegram-gateway:
- **Purpose:** One-on-one voice chat with the agent, accessible from anywhere, owner-only (like Telegram but audio)
- **Flow:** Voice in Mumble → STT (Whisper) → POST /chat → Response text → TTS (Piper) → Voice in Mumble
- **New containers:**
  - `mumble-server` (murmurd) — Mumble voice server, password-protected, single user
  - `mumble-gateway` — Bot that bridges voice to agent-core, with Whisper STT and Piper TTS
- **Key decisions:**
  - Whisper model size (tiny/base for CPU speed vs. small/medium for accuracy)
  - TTS voice selection (Piper has many voice options)
  - Latency budget (STT + LLM + TTS could be several seconds on CPU)
  - Authentication: password-only sufficient since it's one-on-one with owner

**Test criteria:**
- Python execution runs in sandbox, cannot access files outside `/sandbox` or make network calls
- Calendar details TBD
- TTS generates audio file from text input
- Mumble gateway transcribes voice, gets agent response, plays back audio

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

### PHASE 6: Integrations & Infrastructure (Future)

> Goal: Connect the agent to external productivity tools and expand infrastructure capabilities.

#### Chunk 6A: Notion Integration (or similar)

- Read/write pages, databases, and tasks in Notion (or an alternative like Obsidian, Logseq)
- Skill wrapping with full policy enforcement
- Details TBD when we get here

#### Chunk 6B: Docker Management

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
| Default Model | Phi-3 Mini | phi3:latest | 3.8B params, CPU-friendly, fast tasks |
| Reasoning Model | Llama 3.1 | llama3.1:8b | 8B params, complex reasoning/planning |
| Agent API | FastAPI | 0.115.0 | Central /chat endpoint |
| ASGI Server | Uvicorn | 0.32.0 | Serves FastAPI |
| Ollama Client | ollama-python | 0.3.3 | Python client for Ollama API |
| CLI Framework | Click | 8.1.7 | Command-line interface |
| Telegram Bot | python-telegram-bot | 21.5 | Telegram gateway |
| Web UI | Streamlit | latest | Browser-based chat interface |
| LLM Orchestration | LangChain | latest | Used in web UI for ChatOllama, embeddings, text splitting |
| Vector DB | ChromaDB | latest | RAG document storage, chat persistence |
| Embeddings Model | all-minilm | (via Ollama) | Used by web UI for RAG embeddings |
| Cache/Memory | Redis | alpine | Conversation history (active) + job queue (planned) |
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
| `REDIS_URL` | agent-core, telegram-gateway | Redis connection string (default `redis://redis:6379`) |
| `DEFAULT_MODEL` | agent-core | Default Ollama model for fast tasks (default `phi3:latest`) |
| `REASONING_MODEL` | agent-core | Stronger Ollama model for planning/reasoning (default `llama3.1:8b`) |
| `BOOTSTRAP_MODEL` | agent-core | Model used during bootstrap conversation (default `mistral:latest`) |
| `DEEP_MODEL` | agent-core | Large-context model for complex tasks (default `qwen2.5:14b`) |
| `DEEP_NUM_CTX` | agent-core | Context window size for deep model (default `16384`) |
| `NUM_CTX` | agent-core | Context window size for standard models (default `8192`) |
| `HISTORY_TOKEN_BUDGET` | agent-core | Max tokens for conversation history truncation (default `6000`) |

**Future variables (as features are added):**

| Variable | Used By | Description |
|---|---|---|
| `TAVILY_API_KEY` | secret broker → web_search skill | API key for Tavily web search |
| `HEARTBEAT_INTERVAL` | agent-core | Seconds between heartbeat ticks (default 60) |

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
