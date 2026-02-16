# Product Requirements Document (PRD)

## My-Agent: Self-Hosted AI Agent Stack

| Field | Value |
|---|---|
| **Version** | 1.0 (Phase 1 complete) |
| **Last Updated** | 2026-02-09 |
| **Status** | Active development — Phase 1 complete, Phase 2 starting |
| **Author** | Andy |

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Current State of Each Service](#3-current-state-of-each-service)
4. [File Tree](#4-file-tree-current)
5. [Known Issues / Tech Debt](#5-known-issues--tech-debt)
6. [Roadmap](#6-roadmap)
7. [Technology Stack](#7-technology-stack)
8. [Environment Variables](#8-environment-variables)
9. [How to Use This Document](#9-how-to-use-this-document)

---

## 1. Project Overview

**My-Agent** is a self-hosted, multi-interface AI agent stack. It wraps a locally-hosted LLM (Ollama running Phi-3 Mini) behind a central FastAPI service (`agent-core`) and exposes it through multiple frontends: a CLI, a Telegram bot, and a Streamlit web UI. Optional RAG capabilities are provided via ChromaDB.

The project is inspired by the [Openclaw](https://github.com/openclaw) approach to building long-lived, autonomous AI agents — but tailored for a single-user, self-hosted environment.

### Design Principles

| Principle | Description |
|---|---|
| **One brain, many interfaces** | A single agent-core service handles all reasoning; frontends are thin adapters that forward user input and display responses. |
| **Local-first** | All models and data stay on your hardware. No API keys required for core functionality. |
| **Containerized** | Every service runs in Docker. One `docker compose up` brings the entire stack online. |
| **Incremental** | The stack is built in phases. Each phase adds a meaningful capability layer without breaking previous work. |

### Target Environment

- **OS:** Linux, macOS, or Windows (WSL2)
- **Hardware:** CPU-only (no GPU required), 8+ GB RAM recommended
- **Runtime:** Docker Engine + Docker Compose v2

---

## 2. Architecture

### High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        agent_net (Docker bridge)                    │
│                                                                     │
│  ┌─────────────────┐    ┌─────────────────┐    ┌────────────────┐  │
│  │  ollama-runner   │    │   chroma-rag    │    │     redis      │  │
│  │  (LLM engine)   │    │  (vector DB)    │    │   (planned)    │  │
│  │  :11434 int      │    │  :8000 int      │    │  :6379 int     │  │
│  │  no host port    │    │  :8100 host     │    │  no host port  │  │
│  └────────┬─────────┘    └────────┬────────┘    └────────────────┘  │
│           │                       │                                  │
│  ┌────────┴───────────────────────┴────────┐                        │
│  │              agent-core                  │                        │
│  │         (FastAPI central hub)            │                        │
│  │         :8000 int & host                 │                        │
│  └────────┬───────────────────────┬────────┘                        │
│           │                       │                                  │
│  ┌────────┴─────────┐    ┌───────┴────────┐                        │
│  │ telegram-gateway  │    │    web-ui      │                        │
│  │  no host port     │    │  (Streamlit)   │                        │
│  │                   │    │  :8501 int     │                        │
│  │                   │    │  :8501 host    │                        │
│  └───────────────────┘    └───────────────┘                        │
│                                                                     │
│  CLI (runs in-container or on host)                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Map

| Service | Container Name | Image / Build | Internal Port | Host Port | Depends On |
|---|---|---|---|---|---|
| ollama-runner | `ollama-runner` | `ollama/ollama:latest` | 11434 | — | — |
| agent-core | `agent-core` | `./agent-core` (build) | 8000 | 8000 | ollama-runner (healthy) |
| telegram-gateway | `telegram-gateway` | `./telegram-gateway` (build) | — | — | agent-core (healthy) |
| chroma-rag | `chroma-rag` | `chromadb/chroma:latest` | 8000 | 8100 | — |
| web-ui | `web-ui` | `./web-ui` (build) | 8501 | 8501 | agent-core, chroma-rag |
| redis | `redis` | `redis:alpine` | 6379 | — | — |

### Request Flow

```
User input (Telegram / Web UI / CLI)
  → POST http://agent-core:8000/chat
    body: { "message": "...", "model": "phi3:latest", "user_id": "...", "channel": "..." }
  → agent-core checks for "search docs" keyword
    → YES: query ChromaDB for relevant context → forward to Ollama with context
    → NO:  forward directly to Ollama
  → Response JSON: { "response": "..." }
  ← Frontend displays the response to the user
```

---

## 3. Current State of Each Service

### 3.1 ollama-runner

**Status: WORKING**

- Official `ollama/ollama:latest` Docker image
- Model: `phi3:latest` (Phi-3 Mini, 3.8B parameters)
- Persistent Docker volume for model weights
- Healthcheck: `ollama list` every 30 seconds
- No host port exposed (internal only at `:11434`)

### 3.2 agent-core

**Status: WORKING (basic)**

**Files:**

| File | Purpose |
|---|---|
| `app.py` | FastAPI application with `/chat` and `/health` endpoints |
| `cli.py` | Click-based CLI with `chat` and `serve` commands |
| `tools.py` | Tool definitions (stub only — not wired in) |
| `agent` | Shell wrapper script to put CLI on PATH |
| `Dockerfile` | Python 3.12 base image |
| `requirements.txt` | `fastapi`, `uvicorn`, `ollama`, `click`, `requests`, `chromadb` |

**API Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat` | Send a message to the agent and receive a response |
| `GET` | `/health` | Returns `{"status": "ok"}` |

**ChatRequest Schema:**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `message` | `str` | Yes | — | The user's message |
| `model` | `str` | No | `phi3:latest` | Which Ollama model to use |
| `user_id` | `str` | No | `None` | Identifier for the user |
| `channel` | `str` | No | `None` | Source channel (e.g., `telegram`, `cli`, `web`) |

**Current Limitations:**

- **Stateless** — no conversation history; every request is independent
- **No tool execution** — `tools.py` exists but is not called anywhere
- **Single model** — always uses the model specified in the request (no routing logic)
- **RAG routing is keyword-based** — checks for literal string "search docs" in the message
- ~~**`requirements.txt` was missing `chromadb`**~~ — FIXED

### 3.3 telegram-gateway

**Status: WORKING**

**Files:**

| File | Purpose |
|---|---|
| `bot.py` | Telegram bot using `python-telegram-bot` v21.5 |
| `Dockerfile` | Python 3.12-slim base image |
| `requirements.txt` | `python-telegram-bot`, `requests` |

**Features:**

- Boot greeting via `post_init` callback
- Chat ID filtering — only responds to the configured `CHAT_ID`
- Typing indicator while waiting for agent-core response
- Message chunking for long responses (Telegram's 4096 char limit)
- Sends hardcoded model `phi3:latest` in every request to agent-core
- No Redis integration
- No approval keyboards
- No host ports exposed

**Environment Variables:**

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `CHAT_ID` | Authorized Telegram chat ID |
| `AGENT_URL` | URL to agent-core (e.g., `http://agent-core:8000`) |

### 3.4 chroma-rag

**Status: WORKING (infrastructure only)**

- Official `chromadb/chroma:latest` Docker image
- Internal port 8000, host port 8100
- No collections have been created yet
- The `rag_tool()` function in `agent-core/app.py` can query it, but no ingestion pipeline exists

### 3.5 web-ui

**Status: WORKING (with known issue)**

**Files:**

| File | Purpose |
|---|---|
| `app.py` | Full Streamlit chat interface (~442 lines) |
| `Dockerfile` | Python 3.12-slim with system dependencies |
| `requirements.txt` | `streamlit`, `langchain`, `chromadb` |

**Previously known issue (FIXED):** Dockerfile `CMD` referenced the wrong filename.

**Note:** The web UI talks directly to Ollama via LangChain, not through agent-core. This bypasses the central hub and is flagged as tech debt (see Known Issues).

### 3.6 redis

**Status: DECLARED, NOT WIRED IN**

- `redis:alpine` image declared in `docker-compose.yml`
- Container starts successfully
- **Not connected to `agent_net`** — missing `networks` config in compose
- No service currently reads from or writes to Redis
- Intended for conversation memory in Phase 2

---

## 4. File Tree (current)

```
my-agent/
├── docker-compose.yml          # Orchestrates all 6 services
├── .env                        # Secrets: TELEGRAM_TOKEN, CHAT_ID, AGENT_URL
│                                 *** NEVER COMMIT THIS FILE ***
├── agent-core/
│   ├── Dockerfile              # Python 3.12, CLI on PATH
│   ├── requirements.txt        # fastapi, uvicorn, ollama, click, requests
│   ├── app.py                  # FastAPI: /chat, /health, rag_tool()
│   ├── cli.py                  # Click CLI: chat, serve commands
│   ├── tools.py                # Tool definitions (STUB - not wired in)
│   └── agent                   # Shell wrapper for CLI on PATH
│
├── telegram-gateway/
│   ├── Dockerfile              # Python 3.12-slim
│   ├── requirements.txt        # python-telegram-bot, requests
│   └── bot.py                  # Telegram bot: greeting, typing, chunking
│
├── web-ui/
│   ├── Dockerfile              # Python 3.12-slim + system deps
│   ├── requirements.txt        # streamlit, langchain, chromadb
│   └── app.py                  # Streamlit chat UI with RAG
│
├── ollama/                     # Empty directory (placeholder)
│
├── SETUP_GUIDE.md              # Full setup walkthrough for new users
├── VIDEO_OUTLINE.md            # YouTube video outline
└── PRD.md                      # This document
```

---

## 5. Known Issues / Tech Debt

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | Missing `chromadb` in agent-core `requirements.txt` | High | **FIXED** |
| 2 | Web UI Dockerfile `CMD` references wrong file | High | **FIXED** |
| 3 | Redis not on `agent_net` (missing `networks` config) | Medium | OPEN |
| 4 | Web UI bypasses agent-core (talks directly to Ollama via LangChain) | Medium | OPEN |
| 5 | Stale comments in `docker-compose.yml` referencing old config | Low | OPEN |
| 6 | Env var mismatch in telegram bot (wrong variable name) | High | **FIXED** |
| 7 | Duplicate `/chat` route in agent-core `app.py` | High | **FIXED** |
| 8 | Duplicate `Application.builder()` call in telegram bot `bot.py` | Medium | **FIXED** |
| 9 | Stray FastAPI route in web UI `app.py` | Medium | **FIXED** |
| 10 | Port conflict: agent-core and chroma-rag both use internal `:8000` | High | **FIXED** |

---

## 6. Roadmap

### Openclaw Alignment

| Openclaw Pillar | Our Approach | Phase |
|---|---|---|
| Long-lived agent | Docker Compose `restart: unless-stopped`, heartbeat loop | 1 (done) + 5 |
| Gateway architecture | `agent-core` hub + thin adapter services | 1 (done) |
| Model-agnostic routing | Multi-model Ollama + task-based routing | 2 |
| Soul / Persona file | `soul.md` loaded as system prompt on every request | 2 |
| Conversation memory | Redis rolling history | 2 |
| Policy, guardrails, observability | Approval gates, rate limits, tracing, dashboard | 3 |
| Modular skill system | Local `skills/` directory, no external marketplaces | 4 |
| Full system access | Sandboxed tools, allow-lists AND hard deny-lists | 4 |
| Credential security | Secret broker pattern | 4 |
| Heartbeat loop | Background event loop | 5 |
| Jobs & automations | Redis-backed task queue | 5 |
| Persistent memory | Multi-layer Redis + ChromaDB | 6 |
| Self-directed task graph | Agent task lists and subtasks | 7 |
| Proactive behavior rules | Heartbeat + standing instructions | 7 |

### Security Philosophy

Security is treated as a first-class concern, not an afterthought. Phase 3 (Security, Policy & Observability) is deliberately placed **before** Phase 4 (Skills & Tool Calling) so that guardrails are in place before the agent gains the ability to execute tools.

- **Don't nerf capabilities.** The goal is a powerful agent. Security means controlling *how* capabilities are used, not removing them.
- **Sandbox by default.** Every tool runs in a restricted environment. Escalation requires explicit approval.
- **No external skill/plugin marketplaces.** All skills live in a local `skills/` directory under version control. No remote code execution.
- **Allow-lists AND hard deny-lists for shell.** Shell tools use a two-layer filter: an allow-list of permitted commands AND a hard deny-list of commands that are never permitted (e.g., `rm -rf /`, `dd`, `mkfs`).
- **The LLM never sees secrets.** API keys and tokens are injected by a secret broker at execution time, never included in prompts or conversation history.
- **Approval gates for high-risk actions.** Actions like sending emails, modifying files outside the workspace, or executing shell commands require explicit user approval via Telegram inline keyboards or web UI confirmation dialogs.
- **Audit trail.** Every tool invocation, approval decision, and model request is logged with timestamps, user IDs, and channels.
- **Per-skill security.** Each skill declares its own permission requirements. The policy engine enforces them independently.
- **Health dashboard.** A real-time dashboard shows agent status, recent actions, error rates, and security events.
- **Security before capability.** Phase 3 before Phase 4 — always.

**Legend:**

- **Complete** — Built and working
- **Partial** — Infrastructure exists but incomplete
- **Not started** — On the roadmap but no work done

---

### PHASE 1: Foundation (COMPLETE)

> Goal: Basic chat through multiple interfaces, all containerized.

| Layer | Capability | Status |
|---|---|---|
| 1 | Ollama runner with Phi-3 model | Complete |
| 15 | Docker Compose orchestration | Complete |
| 16a | agent-core FastAPI service (`/chat`, `/health`) | Complete |
| 16b | Telegram gateway bot | Complete |
| 16c | Streamlit web UI | Complete |
| — | ChromaDB infrastructure | Complete |
| 8a | Redis container declared | Complete |

All Phase 1 deliverables are complete. The stack can be brought up with `docker compose up` and a user can chat with the agent via CLI, Telegram, or web UI.

---

### PHASE 2: Memory, Identity & Intelligence (NEXT)

> Goal: Give the agent memory, personality, and intelligent model routing.

#### Chunk 2A: Soul / Persona File

| Field | Value |
|---|---|
| **Priority** | HIGH |
| **Scope** | Create `agent-core/soul.md` with the agent's identity, personality, and behavioral guidelines. Load it at startup and prepend as the system prompt on every LLM request. |
| **Key Decisions** | How detailed should the persona be? Should it be per-user or global? |
| **Test Criteria** | The agent should introduce itself by name and exhibit consistent personality traits across all interfaces. |

#### Chunk 2B: Conversation Memory (Redis)

| Field | Value |
|---|---|
| **Priority** | HIGH |
| **Scope** | Wire Redis into `agent-core`. Implement rolling conversation history per user/channel. Add truncation to stay within model context window. Add session TTL for automatic expiry. |
| **Key Decisions** | History format (list of dicts vs. serialized string), max history length, session TTL duration. |
| **Test Criteria** | The agent remembers what was said earlier in the conversation. Restarting the container does not lose history (Redis persistence). |

#### Chunk 2C: Brain-vs-Muscle Model Routing

| Field | Value |
|---|---|
| **Priority** | MEDIUM |
| **Scope** | Pull a second model into Ollama (e.g., a larger reasoning model). Add routing logic to `agent-core` that selects the appropriate model based on task complexity. |
| **Key Decisions** | Which second model to use, routing strategy (keyword-based, classifier, or LLM-as-judge). |

#### Chunk 2D: Fix Remaining Known Issues

| Field | Value |
|---|---|
| **Priority** | HIGH |

Items to address:

- ~~Missing `chromadb` in agent-core requirements~~ — FIXED
- ~~Web UI Dockerfile references wrong file~~ — FIXED
- Add `networks: agent_net` to redis service — OPEN
- ~~Env var mismatch in telegram bot~~ — FIXED
- ~~Duplicate `/chat` route in agent-core~~ — FIXED
- ~~Duplicate `Application.builder()` in telegram bot~~ — FIXED
- ~~Stray FastAPI route in web UI~~ — FIXED
- ~~Port conflict: agent-core and chroma-rag~~ — FIXED
- Clean up stale comments in `docker-compose.yml` — OPEN

---

### PHASE 3: Security, Policy & Observability

> Goal: Establish guardrails and visibility before the agent gains tool-calling abilities.

#### Chunk 3A: Policy Engine

- Define a policy file format (YAML or JSON) for rules and approval gates
- Build a policy evaluation engine in `agent-core`
- Implement approval gates for high-risk actions (Telegram inline keyboards, web UI confirmation)
- Rate limiting per user/channel

#### Chunk 3B: Observability

- Structured logging (JSON) for all services
- Request tracing with correlation IDs across services
- Log aggregation and search
- Error tracking and alerting

#### Chunk 3C: Health Dashboard

- Real-time web dashboard showing agent status
- Recent actions and tool invocations
- Error rates and latency metrics
- Security events and approval decisions

#### Chunk 3D: Container Hardening

- Run containers as non-root users
- Read-only filesystems where possible
- Resource limits (CPU, memory) per container
- Network policies to restrict inter-service communication

#### Chunk 3E: Multi-Tenant

- Per-user policy evaluation
- User identity verification
- Channel-based access control
- Audit trail per user

---

### PHASE 4: Skills & Tool Calling

> Goal: Give the agent the ability to take actions in the world, within the guardrails established in Phase 3.

#### Chunk 4A: Skill Framework

- Define a skill interface (input schema, output schema, permissions)
- Build a skill loader that reads from `agent-core/skills/` directory
- Integrate skill execution with the policy engine
- Skill discovery and help text

#### Chunk 4B: File Tools

- Read, write, list, and search files within a sandboxed workspace
- File diff and patch capabilities
- Directory tree navigation

#### Chunk 4C: Shell Tools

- Execute shell commands within a sandboxed environment
- Allow-list and hard deny-list enforcement
- Output capture and streaming
- Timeout and resource limits

#### Chunk 4D: Web Search & API Tools

- Web search via Tavily or similar API
- HTTP request tool for arbitrary APIs
- Web page content extraction
- Response parsing and summarization

#### Chunk 4E: Secret Broker

- Centralized secret storage (not in environment variables)
- Runtime injection of secrets into tool execution context
- Secrets never appear in prompts, logs, or conversation history
- Per-skill secret access control

---

### PHASE 5: Heartbeat, Jobs & Channels

> Goal: Make the agent proactive and expand its communication channels.

#### Chunk 5A: Heartbeat Loop

- Background event loop that runs on a configurable interval
- Check for pending tasks, scheduled jobs, and standing instructions
- Trigger actions without user input

#### Chunk 5B: Jobs & Automations

- Redis-backed task queue for deferred and recurring jobs
- Cron-like scheduling syntax
- Job status tracking and retry logic
- Job results delivered via configured channel

#### Chunk 5C: Mumble Voice

- Mumble voice server integration
- Speech-to-text input
- Text-to-speech output
- Push-to-talk and always-on modes

#### Chunk 5D: Discord / Slack

- Discord bot gateway (similar to telegram-gateway)
- Slack bot gateway
- Consistent message formatting across channels

---

### PHASE 6: Persistent Memory & Knowledge

> Goal: Give the agent long-term memory and knowledge management capabilities.

#### Chunk 6A: Long-Term Memory

- ChromaDB-backed semantic memory for facts and context
- Automatic extraction of important information from conversations
- Memory retrieval during conversation (RAG pipeline)

#### Chunk 6B: Notes, Tasks & Results

- Structured storage for notes, task lists, and tool execution results
- Searchable and taggable
- Expiry and archival policies

#### Chunk 6C: Multi-Layer Memory

- Layer 1: Redis rolling conversation history (short-term)
- Layer 2: Redis session summaries (medium-term)
- Layer 3: ChromaDB semantic embeddings (long-term)
- Automatic promotion of information between layers

---

### PHASE 7: Autonomy & Planning

> Goal: Enable the agent to plan, execute, and learn from multi-step tasks independently.

#### Chunk 7A: Single-Task Planning

- Break a user request into a sequence of steps
- Execute steps in order, handling errors and retries
- Report progress and results

#### Chunk 7B: Self-Directed Task Graph

- Maintain a task graph with dependencies
- Prioritize and schedule subtasks
- Parallel execution where dependencies allow
- User approval checkpoints for critical decisions

#### Chunk 7C: Proactive Behavior

- Standing instructions that trigger on schedule or events
- Heartbeat-driven periodic tasks
- Context-aware suggestions

#### Chunk 7D: Learning from Feedback

- Track which responses the user corrected or rejected
- Adjust behavior based on feedback patterns
- Surface learned preferences in the persona/soul file

---

## 7. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| LLM Runtime | Ollama | latest | Local model serving |
| Default Model | Phi-3 Mini | phi3:latest (3.8B params) | General-purpose chat and reasoning |
| Agent Core | FastAPI | 0.100+ | Central API hub |
| ASGI Server | Uvicorn | latest | Serves FastAPI application |
| Ollama Client | ollama-python | latest | Python client for Ollama API |
| CLI Framework | Click | latest | Command-line interface |
| Telegram Bot | python-telegram-bot | 21.5 | Telegram gateway |
| Web UI | Streamlit | latest | Browser-based chat interface |
| LLM Orchestration | LangChain | latest | Used by web-ui for ChatOllama and embeddings |
| Vector Database | ChromaDB | latest | RAG and document storage |
| Cache / Memory | Redis | alpine | Conversation history (planned) |
| Container Runtime | Docker Compose | v2 | Service orchestration |
| Language | Python | 3.12 | All custom services |

---

## 8. Environment Variables

All secrets are stored in `.env` in the project root. **Never commit this file.**

### Current (Phase 1)

| Variable | Service | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | telegram-gateway | Bot token from Telegram @BotFather |
| `CHAT_ID` | telegram-gateway | Authorized Telegram chat ID |
| `AGENT_URL` | telegram-gateway | URL to agent-core (e.g., `http://agent-core:8000`) |

### Future (Phase 2+)

| Variable | Service | Phase | Description |
|---|---|---|---|
| `REDIS_URL` | agent-core | 2 | Redis connection string |
| `DEFAULT_MODEL` | agent-core | 2 | Default model for general requests |
| `REASONING_MODEL` | agent-core | 2 | Model for complex reasoning tasks |
| `TAVILY_API_KEY` | agent-core | 4 | API key for Tavily web search |
| `HEARTBEAT_INTERVAL` | agent-core | 5 | Seconds between heartbeat loop ticks |

---

## 9. How to Use This Document

This PRD is designed to be fed to an AI coding assistant at the start of every development session. Follow these steps:

1. **Give the AI this entire document** as context at the start of your session.
2. **Specify which chunk** you want to work on (e.g., "Implement Chunk 2B: Conversation Memory").
3. **Point it at the relevant files** — the file tree and service descriptions tell it exactly what exists and where.
4. **The known issues section** tells it what is broken before it starts, so it does not re-introduce fixed bugs or miss open problems.
5. **The security philosophy section** is mandatory reading — every implementation must respect it.

Each chunk is scoped to be completable in a single focused session. Chunks within a phase can generally be done in any order, but phases are sequential (Phase 2 before Phase 3, etc.).

When a chunk is completed, update this document:
- Move the chunk status to Complete
- Update the "Current State" section for any modified services
- Add any new known issues discovered during implementation
- Update the file tree if new files were added
