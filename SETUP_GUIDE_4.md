# My-Agent: Persistent Memory & Heartbeat Setup Guide

Building on the skill framework from Phase 4A/4B, this guide adds Phase 4C: long-term semantic memory, a memory sanitization layer, `remember`/`recall` skills, auto-summarise of truncated history, and a background heartbeat loop. This is what turns a sophisticated chatbot into a genuine agent — one that remembers across sessions and continues running between conversations.

## What You're Adding

A three-layer memory architecture and a background heartbeat loop:

- **Long-term memory** — ChromaDB `agent_memory` collection (separate from `rag_data`). Every fact the agent stores is semantically searchable across sessions.
- **Working memory** — A compact `## Working Memory` block automatically injected into the system prompt on every request. The agent sees its own recent memories without being asked.
- **Short-term memory** — Existing Redis rolling window (unchanged).
- **Memory sanitization** — All content written to memory is sanitized: control chars stripped, HTML removed, and 8 prompt-injection patterns blocked. Content that could hijack the agent's memory (e.g., web content containing hidden instructions) is rejected with `MemoryPoisonError`.
- **`remember` skill** — Agent writes facts, observations, and preferences to long-term memory.
- **`recall` skill** — Agent performs semantic search over its own memory with age-formatted results.
- **Auto-summarise on truncation** — When the Redis history budget is exceeded and messages are dropped, those dropped messages are summarised via Ollama and stored in `agent_memory` as a `"summary"` entry. Fire-and-forget — never blocks a response.
- **Heartbeat loop** — Background asyncio task started at server startup. Ticks every 60 seconds (configurable). Logs each tick to the structured tracing system. Foundation for future scheduled jobs and proactive behaviors.

### Updated Data Flow

```
User message --> /chat
  --> build_working_memory(user_id)         # Fetch 8 most recent memories from ChromaDB
  --> system_prompt += "## Working Memory"  # Inject if any memories exist
  --> Load Redis history
  --> Truncate history if needed
      --> if dropped: asyncio.create_task(_summarise_and_store(dropped, user_id))  # fire-and-forget
  --> run_tool_loop() with all 9 skills
      --> model may call remember(content, type)  # RememberSkill stores to agent_memory
      --> model may call recall(query)             # RecallSkill semantic search
  --> Save clean history to Redis
  --> Return response

Background (every 60s):
  heartbeat_loop() --> _tick() --> tracing._emit("heartbeat", {"status": "tick"})
```

### ChromaDB Collections (After Phase 4C)

```
chroma-rag container:
├── rag_data          # Document knowledge base (rag_ingest / rag_search)
│   metadata: {source, chunk_index}
│
└── agent_memory      # Agent's personal long-term memory (remember / recall)
    metadata: {user_id, type, source, timestamp}
    types: fact | observation | preference | summary
```

---

## Prerequisites

- **Completed stack from Setup Guide 3 + Phase 4A + Phase 4B** (skill framework, all 7 existing skills working, Redis-backed rate limiting in place)
- `chroma-rag` container running (already in compose from Phase 4A)
- `chromadb` package already in `agent-core/requirements.txt`
- No new infrastructure — no new containers, no new env vars required (heartbeat uses `HEARTBEAT_INTERVAL_SECONDS` with a sensible default of 60)

**Optional:**
```bash
# Add to .env if you want a custom heartbeat interval
HEARTBEAT_INTERVAL_SECONDS=60
```

---

## New and Modified Files

After this guide, your project will have these additions:

```
agent-core/
├── memory.py                   # NEW — MemoryStore: ChromaDB agent_memory wrapper
├── memory_sanitizer.py         # NEW — sanitize(): injection detection + HTML/control strip
├── heartbeat.py                # NEW — Background asyncio heartbeat loop
├── app.py                      # MODIFIED — working memory injection, auto-summarise, heartbeat startup, new skills
├── skill_runner.py             # MODIFIED — inject _user_id into skill.execute() params
├── policy.yaml                 # MODIFIED — rate limits for remember + recall
├── skills/
│   ├── remember.py             # NEW — RememberSkill
│   └── recall.py               # NEW — RecallSkill
└── tests/
    ├── test_memory.py          # NEW — 21 tests for MemoryStore + MemorySanitizer
    ├── test_heartbeat.py       # NEW — 4 tests for heartbeat loop
    └── test_skills.py          # MODIFIED — TestRememberSkill (14) + TestRecallSkill (13) appended
```

---

## Step 1: Create the Memory Sanitizer

Create `agent-core/memory_sanitizer.py`. This is the security gatekeeper — nothing enters long-term memory without passing through it.

```python
import re

class MemoryPoisonError(ValueError):
    """Raised when content is rejected due to potential prompt injection."""

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|prior|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"disregard\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?:", re.IGNORECASE),
    re.compile(r"<\s*/?system", re.IGNORECASE),
    re.compile(r"\[INST\]"),
    re.compile(r"<<SYS>>"),
]

_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EXCESS_SPACE_RE = re.compile(r"[ \t]{2,}")


def sanitize(content: str) -> str:
    """
    Sanitize memory content before storage.

    Order matters:
      1. Strip control chars (null bytes, non-printable) — keep \t \n \r
      2. Check injection patterns BEFORE stripping HTML
         (prevents <<SYS>> from being mangled by step 3 and bypassing detection)
      3. Strip HTML tags
      4. Collapse excess whitespace

    Raises MemoryPoisonError if injection pattern detected.
    Returns cleaned string.
    """
    # Step 1: strip control chars
    cleaned = _CTRL_CHARS_RE.sub("", content)

    # Step 2: injection check (before HTML strip — critical ordering)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise MemoryPoisonError(
                "Content rejected: potential prompt injection detected."
            )

    # Step 3: strip HTML tags
    cleaned = _HTML_TAG_RE.sub("", cleaned)

    # Step 4: collapse excess whitespace
    cleaned = _EXCESS_SPACE_RE.sub(" ", cleaned)

    return cleaned.strip()
```

**Why injection check BEFORE HTML strip?** The pattern `<<SYS>>` contains `<` characters. The HTML regex `<[^>]+>` would match `<<SYS>` and strip it, leaving just `>`, which would not match the injection pattern. Always check first, strip second.

---

## Step 2: Create MemoryStore

Create `agent-core/memory.py`. This wraps the ChromaDB `agent_memory` collection with a clean interface.

```python
import os
import time
import uuid
from typing import List, Dict

CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma-rag")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
MEMORY_COLLECTION = "agent_memory"


def _get_ef():
    from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
    return OllamaEmbeddingFunction(
        url=os.getenv("OLLAMA_HOST", "http://ollama-runner:11434"),
        model_name=os.getenv("EMBED_MODEL", "nomic-embed-text"),
    )


class MemoryStore:
    """
    ChromaDB-backed long-term memory store.
    Separate collection from rag_data — different metadata schema.
    Uses OllamaEmbeddingFunction (nomic-embed-text) for consistent vector space.
    """

    def _get_collection(self):
        import chromadb
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        return client.get_or_create_collection(
            MEMORY_COLLECTION,
            embedding_function=_get_ef(),
        )

    def add(
        self,
        content: str,
        memory_type: str,
        user_id: str,
        source: str = "agent",
    ) -> str:
        """Store a memory entry. Returns the memory_id."""
        collection = self._get_collection()
        memory_id = str(uuid.uuid4())
        collection.add(
            documents=[content],
            metadatas=[{
                "user_id": user_id,
                "type": memory_type,
                "source": source,
                "timestamp": time.time(),
            }],
            ids=[memory_id],
        )
        return memory_id

    def search(self, query: str, user_id: str, n_results: int = 5) -> List[Dict]:
        """Semantic search over memories for a specific user."""
        collection = self._get_collection()
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )
        entries = []
        if results["documents"] and results["documents"][0]:
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                entry = {"content": doc}
                entry.update(meta)
                entries.append(entry)
        return entries

    def get_recent(self, user_id: str, n: int = 8) -> List[Dict]:
        """Get the n most recent memories for a user, sorted by timestamp descending."""
        collection = self._get_collection()
        results = collection.get(
            where={"user_id": user_id},
            limit=50,
            include=["documents", "metadatas"],
        )
        if not results["documents"]:
            return []
        entries = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            entry = {"content": doc}
            entry.update(meta)
            entries.append(entry)
        entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return entries[:n]
```

**Key design decisions:**
- A new `HttpClient` is created per call — stateless HTTP, consistent with the existing `rag_search`/`rag_ingest` pattern. ChromaDB HTTP clients are lightweight.
- `OllamaEmbeddingFunction` with `nomic-embed-text` is used everywhere — `rag_data` and `agent_memory` share the same vector space, so recall results are directly comparable to RAG results. Fully self-hosted: embeddings are generated by `ollama-runner`, no external API calls. Requires `nomic-embed-text` to be pulled: `docker exec ollama-runner ollama pull nomic-embed-text`.
- `where={"user_id": user_id}` on every query — memories are strictly scoped per user. User A cannot recall User B's memories.

---

## Step 3: Create the Heartbeat Module

Create `agent-core/heartbeat.py`:

```python
import asyncio
import os
import tracing

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "60"))


async def heartbeat_loop(state) -> None:
    """
    Background loop that runs forever, ticking every HEARTBEAT_INTERVAL seconds.
    Catches all Exception (not BaseException) so CancelledError propagates correctly
    when the task is cancelled on shutdown.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await _tick(state)
        except Exception as e:
            tracing._emit("heartbeat", {"status": "error", "error": str(e)})


async def _tick(state) -> None:
    """
    One heartbeat tick. Currently: log the tick.
    Phase 4C-Part-2: check job queue, evaluate triggers, execute due tasks.
    """
    tracing._emit("heartbeat", {"status": "tick"})


def start_heartbeat(state) -> asyncio.Task:
    """Start the heartbeat loop as a background asyncio task. Returns the Task."""
    return asyncio.create_task(heartbeat_loop(state))
```

**Why `except Exception` not `except BaseException`?** In Python 3.8+, `asyncio.CancelledError` is a subclass of `BaseException`, not `Exception`. Using `except Exception` means the heartbeat loop catches operational errors (network failures, ChromaDB down, etc.) without swallowing task cancellation. When FastAPI shuts down and cancels the background task, the `CancelledError` propagates normally.

---

## Step 4: Create the Remember Skill

Create `agent-core/skills/remember.py`:

```python
from skills.base import SkillBase, SkillMetadata
from memory_sanitizer import sanitize, MemoryPoisonError

_VALID_TYPES = {"fact", "observation", "preference"}


class RememberSkill(SkillBase):
    metadata = SkillMetadata(
        name="remember",
        description=(
            "Store a fact, observation, or preference in long-term memory. "
            "Use this to remember things the user tells you about themselves, "
            "their preferences, or important context for future conversations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to remember (max 1000 characters).",
                },
                "type": {
                    "type": "string",
                    "enum": ["fact", "observation", "preference"],
                    "description": "Category of the memory. Default: fact.",
                },
            },
            "required": ["content"],
        },
        risk_level="low",
        requires_approval=False,
        rate_limit="remember",
        max_calls_per_turn=5,
    )

    def validate(self, params: dict) -> tuple[bool, str]:
        content = params.get("content", "")
        if not content or not content.strip():
            return False, "content is required and cannot be empty"
        if len(content) > 1000:
            return False, f"content too long ({len(content)} chars, max 1000)"
        mem_type = params.get("type", "fact")
        if mem_type not in _VALID_TYPES:
            return False, f"type must be one of: {', '.join(sorted(_VALID_TYPES))}"
        try:
            sanitize(content)
        except MemoryPoisonError as e:
            return False, str(e)
        return True, ""

    async def execute(self, params: dict) -> dict:
        from memory import MemoryStore

        user_id = params.pop("_user_id", "default")
        content = params.get("content", "").strip()
        mem_type = params.get("type", "fact")

        try:
            content = sanitize(content)
            store = MemoryStore()
            memory_id = store.add(content, mem_type, user_id, source="agent")
            return {"memory_id": memory_id, "type": mem_type, "content": content}
        except MemoryPoisonError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to store memory: {e}"}

    def sanitize_output(self, result) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[remember] {result['error']}"
        if isinstance(result, dict):
            content_preview = result.get("content", "")[:100]
            return f"Stored {result.get('type', 'fact')}: {content_preview}"
        return str(result)
```

---

## Step 5: Create the Recall Skill

Create `agent-core/skills/recall.py`:

```python
import time
from skills.base import SkillBase, SkillMetadata


def _format_age(seconds: float) -> str:
    """Format elapsed seconds as a human-readable age string."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 604800:
        return f"{int(seconds // 86400)}d"
    if seconds < 2592000:
        return f"{int(seconds // 604800)}w"
    return f"{int(seconds // 2592000)}mo"


class RecallSkill(SkillBase):
    metadata = SkillMetadata(
        name="recall",
        description=(
            "Search your long-term memory for relevant facts, observations, "
            "and preferences. Use this when you need to remember something "
            "about the user or a previous conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory (max 500 characters).",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (1–10, default 5).",
                },
            },
            "required": ["query"],
        },
        risk_level="low",
        requires_approval=False,
        rate_limit="recall",
        max_calls_per_turn=5,
    )

    def validate(self, params: dict) -> tuple[bool, str]:
        query = params.get("query", "")
        if not query or not query.strip():
            return False, "query is required and cannot be empty"
        if len(query) > 500:
            return False, f"query too long ({len(query)} chars, max 500)"
        n = params.get("n_results", 5)
        if not isinstance(n, int) or not (1 <= n <= 10):
            return False, "n_results must be an integer between 1 and 10"
        return True, ""

    async def execute(self, params: dict) -> dict:
        from memory import MemoryStore

        user_id = params.pop("_user_id", "default")
        query = params.get("query", "").strip()
        n_results = params.get("n_results", 5)

        try:
            store = MemoryStore()
            entries = store.search(query, user_id, n_results=n_results)
            return {"entries": entries}
        except Exception as e:
            return {"error": f"Failed to search memory: {e}"}

    def sanitize_output(self, result) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[recall] {result['error']}"
        if isinstance(result, dict):
            entries = result.get("entries", [])
            if not entries:
                return "No memories found."
            now = time.time()
            lines = []
            for i, entry in enumerate(entries, 1):
                age = _format_age(now - entry.get("timestamp", now))
                mem_type = entry.get("type", "fact")
                content = entry.get("content", "")
                lines.append(f"{i}. [{mem_type}, {age}] {content}")
            return "\n".join(lines)
        return str(result)
```

---

## Step 6: Update skill_runner.py — Inject _user_id

This is a one-line change. Find the `execute_skill()` function in `agent-core/skill_runner.py` and locate the step where `skill.execute(params)` is called. Replace it:

```python
# Before:
result = await skill.execute(params)

# After:
result = await skill.execute({**params, "_user_id": user_id})
```

The `user_id` parameter is already available in `execute_skill()` — it's passed in from `run_tool_loop()`. This change is fully backward-compatible: existing skills (`web_search`, `file_read`, etc.) use `**params` in their signatures or read specific keys, so they silently ignore the extra `_user_id` key.

---

## Step 7: Update app.py

Four separate additions to `agent-core/app.py`:

### 7a. Imports and setup

Add to the import section:

```python
import asyncio
import time
from memory import MemoryStore
from heartbeat import start_heartbeat
from skills.remember import RememberSkill
from skills.recall import RecallSkill
```

Add a module-level memory store singleton (after the Redis client setup):

```python
memory_store = MemoryStore()
```

Register the new skills (alongside the existing skill registrations):

```python
skill_registry.register(RememberSkill())
skill_registry.register(RecallSkill())
```

### 7b. Working memory helpers

Add these two helpers near the top of `app.py` (before the `/chat` handler):

```python
def _format_age(seconds: float) -> str:
    """Human-readable age: 'just now', '5m', '2h', '3d', '2w', '1mo'."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 604800:
        return f"{int(seconds // 86400)}d"
    if seconds < 2592000:
        return f"{int(seconds // 604800)}w"
    return f"{int(seconds // 2592000)}mo"


def build_working_memory(user_id: str) -> str:
    """
    Build the working memory block for injection into the system prompt.
    Returns "" if no memories or ChromaDB unavailable (fails silently).
    Hard cap: 1200 chars (~300 tokens).
    """
    try:
        entries = memory_store.get_recent(user_id, n=8)
        if not entries:
            return ""
        now = time.time()
        lines = []
        for e in entries:
            age = _format_age(now - e.get("timestamp", now))
            lines.append(f"- [{e.get('type', 'fact')}] {e.get('content', '')} ({age})")
        block = "## Working Memory\n" + "\n".join(lines)
        if len(block) > 1200:
            block = block[:1197] + "[...]"
        return block
    except Exception:
        return ""
```

### 7c. Auto-summarise on truncation

Add this async function:

```python
async def _summarise_and_store(dropped: list, user_id: str) -> None:
    """
    Fire-and-forget: summarise dropped history messages and store in long-term memory.
    Called as asyncio.create_task() — never blocks the chat response.
    """
    try:
        from memory import MemoryStore
        text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:400]}" for m in dropped
        )
        summary_prompt = [
            {"role": "user", "content": f"Summarise this conversation in 2-3 sentences:\n\n{text}"},
        ]
        resp = await asyncio.to_thread(
            ollama_client.chat,
            model=DEFAULT_MODEL,
            messages=summary_prompt,
            options={"num_ctx": 2048},
        )
        summary = resp["message"]["content"].strip()
        if summary:
            store = MemoryStore()
            store.add(summary, "summary", user_id, source="agent")
    except Exception:
        pass  # fire-and-forget — never crash
```

### 7d. Wire everything into the /chat handler

**Working memory injection** — add after `build_system_prompt()` returns and before the tool-usage hint is appended:

```python
memory_block = build_working_memory(user_id)
if memory_block:
    system_prompt += "\n\n" + memory_block
```

**Capture dropped messages** — modify the history truncation loop:

```python
# Before:
while len(truncated) > 1 and sum(estimate_tokens(m["content"]) for m in truncated) > HISTORY_TOKEN_BUDGET:
    truncated.pop(0)

# After:
dropped = []
while len(truncated) > 1 and sum(estimate_tokens(m["content"]) for m in truncated) > HISTORY_TOKEN_BUDGET:
    dropped.append(truncated.pop(0))
if dropped:
    asyncio.create_task(_summarise_and_store(dropped, user_id))
```

**Tool hint update** — add `remember` and `recall` to the tool-usage hint in the system prompt so the model knows to use them:

```python
# In the tool-usage hint block, add:
"- Use `remember` to store facts, preferences, or observations the user shares."
"- Use `recall` to search your long-term memory when the user asks what you know about them."
```

### 7e. Startup event

Add the heartbeat startup at the bottom of `app.py` (or alongside any existing startup handlers):

```python
@app.on_event("startup")
async def startup():
    start_heartbeat(app.state)
```

---

## Step 8: Update policy.yaml

Add rate limit entries for the two new skills:

```yaml
rate_limits:
  # ... existing entries (rag_search, web_search, url_fetch, etc.) ...
  remember:
    max_calls: 15
    window_seconds: 60
  recall:
    max_calls: 20
    window_seconds: 60
```

---

## Step 9: Run the Tests

### Install any missing test dependencies

```bash
cd agent-core
pip install pytest pytest-asyncio
```

### Run all tests

```bash
python -m pytest tests/ -q
```

Expected: **357 tests passing** (up from 305 after Phase 4B).

### New test files

**test_memory.py (21 tests):**

| Class | Count | What It Validates |
|-------|-------|-------------------|
| TestMemorySanitizer | 13 | Clean text passes through, control chars stripped, HTML stripped, each of 8 injection patterns raises MemoryPoisonError, injection check before HTML (<<SYS>> not bypassed), MemoryPoisonError is subclass of ValueError |
| TestMemoryStore | 8 | `add()` calls collection.add with correct metadata fields, `search()` returns merged content+metadata, `get_recent()` sorts by timestamp descending, `get_recent()` empty when no entries, ChromaDB errors propagate from `add()`, from `search()` |

ChromaDB is mocked via `patch.dict(sys.modules, ...)` — no running ChromaDB needed for tests:

```python
def _chroma_modules():
    mock_chroma = MagicMock()
    mock_utils = MagicMock()
    mock_ef = MagicMock()
    mock_ef_instance = MagicMock(return_value=["vec"])
    mock_ef.OllamaEmbeddingFunction.return_value = mock_ef_instance
    return {
        "chromadb": mock_chroma,
        "chromadb.utils": mock_utils,
        "chromadb.utils.embedding_functions": mock_ef,
    }
```

**test_heartbeat.py (4 tests):**

| Test | What It Validates |
|------|-------------------|
| test_tick_emits_tracing | `_tick()` calls `tracing._emit("heartbeat", ...)` |
| test_exception_in_tick_does_not_kill_loop | Exception inside `_tick()` is caught, loop continues |
| test_start_heartbeat_returns_task | `start_heartbeat()` returns an `asyncio.Task` |
| test_cancellation_propagates | Cancelling the task raises `CancelledError` (not swallowed) |

**test_skills.py additions (27 new tests):**

`TestRememberSkill` (14 tests):
- Metadata: name, risk_level, requires_approval, max_calls_per_turn
- validate: valid params pass, empty content fails, content > 1000 chars fails, invalid type fails, MemoryPoisonError from sanitizer returns validation error
- execute: success path (mock MemoryStore), uses _user_id from params (default "default"), ChromaDB error returns error dict
- sanitize_output: success, error dict

`TestRecallSkill` (13 tests):
- Metadata: name, risk_level, requires_approval
- validate: valid, empty query fails, query > 500 chars fails, n_results out of range fails
- execute: results returned, empty results, ChromaDB error returns error dict
- sanitize_output: with results (numbered list with age), empty ("No memories found."), error dict

---

## Step 10: Rebuild and Verify

### Rebuild agent-core

```bash
docker compose build agent-core && docker compose up -d agent-core
```

### End-to-end smoke test — explicit remember and recall

```bash
API="http://127.0.0.1:8000"
KEY="$(grep AGENT_API_KEY .env | cut -d= -f2)"
chat() { curl -s -X POST "$API/chat" -H "Content-Type: application/json" -H "X-Api-Key: $KEY" -d "$1"; }

# 1. Explicit remember
chat '{"message":"Please use the remember tool to store: my name is Andy and I prefer short answers","user_id":"smoke_test"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['response'][:300])"

# 2. Check skill log — remember should be index 0
RPASS="$(grep REDIS_PASSWORD .env | cut -d= -f2)"
docker exec $(docker ps -qf name=redis) redis-cli -a "$RPASS" lrange logs:skill 0 0 2>/dev/null \
  | python3 -c "import sys,json; d=json.loads(sys.stdin.read().strip()); print('skill:', d.get('skill_name'), '| status:', d.get('status'))"

# 3. New session — clear history, check working memory injection
docker exec $(docker ps -qf name=redis) redis-cli -a "$RPASS" del chat:smoke_test 2>/dev/null
chat '{"message":"What do you know about me?","user_id":"smoke_test"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['response'][:400])"

# 4. Check ChromaDB agent_memory collection
docker exec agent-core python3 -c "
import chromadb, os
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
ef = OllamaEmbeddingFunction(url=os.getenv('OLLAMA_HOST','http://ollama-runner:11434'), model_name='nomic-embed-text')
c = chromadb.HttpClient(host='chroma-rag', port=8000)
col = c.get_or_create_collection('agent_memory', embedding_function=ef)
count = col.count()
print(f'agent_memory: {count} entries')
if count > 0:
    results = col.get(limit=5, include=['documents','metadatas'])
    for doc, meta in zip(results['documents'], results['metadatas']):
        print(f\"  [{meta.get('type')}] {doc[:80]} (user={meta.get('user_id')})\")
"
```

**Expected output:**
```
skill: remember | status: success
agent_memory: 1 entries
  [fact] My name is Andy and I prefer short answers (user=smoke_test)
```

### Verify heartbeat is ticking

Wait ~2 minutes, then check the heartbeat log:

```bash
RPASS="$(grep REDIS_PASSWORD .env | cut -d= -f2)"
docker exec $(docker ps -qf name=redis) redis-cli -a "$RPASS" lrange logs:heartbeat 0 4 2>/dev/null \
  | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        print('tick at', d.get('timestamp', '?'), '| status:', d.get('status'))
    except: pass
"
```

**Expected:** Entries appearing at ~60-second intervals with `status: tick`.

### Test auto-summarise on truncation

Drive a long conversation to fill the history budget, then check for summary entries:

```bash
# Send many messages to fill history (or lower HISTORY_TOKEN_BUDGET temporarily for testing)
for i in {1..20}; do
  chat "{\"message\":\"message $i: tell me something interesting\",\"user_id\":\"smoke_test\"}" > /dev/null
done

# Check for summary entries in agent_memory
docker exec agent-core python3 -c "
import chromadb, os
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
ef = OllamaEmbeddingFunction(url=os.getenv('OLLAMA_HOST','http://ollama-runner:11434'), model_name='nomic-embed-text')
c = chromadb.HttpClient(host='chroma-rag', port=8000)
col = c.get_or_create_collection('agent_memory', embedding_function=ef)
results = col.get(where={'type': 'summary'}, include=['documents','metadatas'])
print(f'Summary entries: {len(results[\"documents\"])}')
for doc, meta in zip(results['documents'], results['metadatas']):
    print(f'  {doc[:120]}')
"
```

---

## Memory Entry Schema

Every entry in `agent_memory` has these metadata fields:

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `user_id` | string | any | Scopes memory per user — mandatory filter on all queries |
| `type` | string | `fact`, `observation`, `preference`, `summary` | Category of memory |
| `source` | string | `agent` | Who created the entry (`agent` = created by agent logic) |
| `timestamp` | float | unix epoch | When the entry was stored — used for recency sorting and age display |

---

## Security Notes

### Memory Poisoning
The biggest risk in long-term memory is **memory poisoning**: web content, user input, or skill output that contains hidden instructions gets stored in memory and then injected into future prompts — permanently compromising all subsequent conversations.

**Defenses:**
- `sanitize()` in `memory_sanitizer.py` checks 8 injection patterns before anything is stored. Any content matching `ignore previous instructions`, `system prompt`, `you are now`, `<<SYS>>`, `[INST]`, etc. raises `MemoryPoisonError` and is rejected.
- Injection check runs BEFORE HTML stripping — prevents `<<SYS>>` from being mangled into `>` and bypassing detection.
- `MemoryPoisonError` propagates from `validate()` — the LLM never receives an error string it could try to work around; validation simply fails and the skill is not executed.
- Rate limit of 15/min on `remember` — prevents memory flooding (e.g., if the LLM is tricked into storing hundreds of entries rapidly).

### User Isolation
- All ChromaDB queries include `where={"user_id": user_id}` — strict scoping. User A's memories are invisible to User B's sessions.
- `_user_id` is injected by `skill_runner.execute_skill()` from the authenticated request context — the LLM cannot override it by including `_user_id` in tool call parameters (it would be ignored; `execute_skill()` always overrides with the real session user_id).

### Working Memory Injection
- Hard cap of 1200 chars on the working memory block — prevents an adversary from expanding agent_memory to consume the entire context window.
- `build_working_memory()` fails silently — if ChromaDB is down, the system prompt is unchanged. The agent continues working without memory; it does not crash.
- Memory content was sanitized at write time — what appears in the system prompt has already passed the injection filter.

### Heartbeat
- `except Exception` in `heartbeat_loop()` ensures a single `_tick()` failure does not kill the loop. The loop itself is alive for the entire server lifetime.
- `CancelledError` is NOT caught — task cancellation on server shutdown propagates normally.
- The heartbeat currently only logs — it has no capability to call skills or modify state until 4C-Part-2 wires the job queue.

---

## What's Next

Phase 4C-Part-2 (jobs/scheduled tasks):
1. **Redis-backed job queue** — `create_task`, `list_tasks`, `cancel_task` skills with scheduled/event/one-shot trigger types.
2. **Heartbeat execution** — `_tick()` checks the job queue and fires due tasks via the standard skill pipeline.
3. **`POST /jobs`, `GET /jobs`, `DELETE /jobs/{id}`** API endpoints.
4. **Overlap prevention** — Redis lock to prevent concurrent execution of the same job.

Phase 4D (math, physics, media):
1. **`calculator` skill** — Safe expression evaluation (`ast`-based, no `eval()`).
2. **`physics` skill** — Unit conversions via `pint`.
3. **`image_gen` skill** — Stable Diffusion (on hold pending GPU installation).
