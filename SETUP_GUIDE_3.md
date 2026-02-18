# My-Agent: Observability & Structured Tracing Setup Guide

Building on the policy engine and identity system from [Setup Guide 2](SETUP_GUIDE_2.md), this guide adds structured JSON tracing so every request, policy decision, and approval event is traceable. Before adding skills or a dashboard, we need to see what the agent is doing.

## What You're Adding

A structured observability layer that replaces ad-hoc print statements with JSON-formatted tracing:

- **Per-request trace IDs** - Every `/chat` request gets a 16-character hex trace ID via `contextvars`, automatically shared by all downstream calls
- **JSON logging to stdout** - Docker captures structured logs instead of unstructured print output
- **Redis log storage** - Every event is pushed to Redis lists for the dashboard to query
- **Five event emitters** - Chat requests/responses, skill calls, policy decisions, approval events
- **Sensitive data redaction** - Passwords, tokens, secrets, and API keys are automatically stripped
- **Ollama metrics** - Token counts and duration extracted from model responses

### Updated Data Flow

```
User message --> /chat
  --> new_trace(user_id, channel)         # Generate trace ID, set context vars
  --> route_model()
  --> log_chat_request(message, model)    # JSON to stdout + Redis logs:chat + logs:all
  --> Ollama call
  --> log_chat_response(model, metrics)   # Token counts, duration
  --> approval events (if bootstrap)      # log_approval_event() in approval.py
  --> Response JSON: { response, model, trace_id }
```

All events within a single request share the same `trace_id`, making it easy to follow a message through the entire processing chain.

### Redis Log Structure

```
logs:all            # Firehose — last 1000 entries of all types
logs:chat           # Chat request/response events — last 500
logs:skill          # Skill invocation events — last 500
logs:policy         # Policy decision events — last 500
logs:approval       # Approval gate events — last 500
```

Each entry is a JSON string pushed via `LPUSH` (newest first). Lists are trimmed via `LTRIM` after each push to maintain fixed-size retention.

---

## Prerequisites

- **Completed stack from Setup Guide 2** (policy engine, approval gates, identity system)
- No new infrastructure — no new containers, no new pip packages
- Uses only Python stdlib: `logging`, `contextvars`, `json`, `uuid`, `time`

---

## New and Modified Files

After this guide, your project will have these changes:

```
agent-core/
├── tracing.py                  # NEW — Core tracing module
├── app.py                      # MODIFIED — Wired tracing, replaced prints, trace_id in response
├── approval.py                 # MODIFIED — Tracing hooks in create_request() and resolve()
└── tests/
    ├── conftest.py             # MODIFIED — FakeRedis extended with list ops
    └── test_tracing.py         # NEW — 49 tests across 10 test classes
```

---

## Step 1: Create the Tracing Module

Create `agent-core/tracing.py`. This is the core observability module (~240 lines). Key components:

### Context Variables

Three `contextvars.ContextVar` instances hold per-request state:

```python
_trace_id = contextvars.ContextVar("trace_id", default="")
_user_id = contextvars.ContextVar("user_id", default="")
_channel = contextvars.ContextVar("channel", default="")
```

These are set once at request entry via `new_trace()` and automatically available to all downstream code without threading parameters through every function signature.

### JSON Formatter

```python
class JSONFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "structured_data"):
            entry.update(record.structured_data)
        return json.dumps(entry, default=str)
```

Single-line JSON output to stdout. Docker's log driver captures these automatically.

### Setup Function

```python
def setup_logging(redis_client=None) -> logging.Logger:
```

Call once at startup. Stores the Redis client reference for log pushing. Creates a logger named `agent.tracing` with the JSON formatter. Skips duplicate handlers on repeated calls.

### Trace Management

- `new_trace(user_id, channel)` — Generates a 16-character hex trace ID (`uuid4().hex[:16]`), sets all three context vars, returns the trace ID
- `get_trace_id()` — Returns the current trace ID (empty string if none set)
- `get_trace_context()` — Returns `{trace_id, user_id, channel}` dict

### Event Emitters

Five public functions, all returning the JSON string that was emitted:

| Function | Event Type | Key Fields |
|----------|-----------|------------|
| `log_chat_request(message, model, **extra)` | `chat` | `message_preview` (truncated to 100 chars), `model` |
| `log_chat_response(model, response_preview, eval_count, prompt_eval_count, total_duration_ms, **extra)` | `chat` | `metrics` dict with token counts and duration |
| `log_skill_call(skill_name, params, **extra)` | `skill` | `skill_name`, `params` (sanitized) |
| `log_policy_decision(action, zone, decision, risk_level, reason, **extra)` | `policy` | Full policy result fields |
| `log_approval_event(approval_id, action, zone, risk_level, status, description, response_time_ms, **extra)` | `approval` | `approval_id`, `status`, optional `response_time_ms` |

Every emitter automatically includes `trace_id`, `user_id`, `channel`, and `timestamp` from context.

### Internal Emit + Redis Push

```python
def _emit(event_type, data):
    # 1. Build entry dict with context + data
    # 2. Log to stdout via JSON formatter
    # 3. Push to Redis (logs:all + logs:<type>)
    # 4. LTRIM both lists for retention
```

Redis push is wrapped in try/except — if Redis is down, stdout still works. Tracing never crashes a request.

### Sanitization

```python
_SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "apikey", "api_secret"}

def _sanitize(params):   # Replaces sensitive values with "***REDACTED***"
def _truncate(value, max_len=200):  # Caps string fields
```

Case-insensitive key matching. Supports nested dicts. Skill parameters are automatically sanitized before logging.

### Dashboard Query Helper

```python
def get_recent_logs(redis_client, log_type="all", count=50, offset=0):
```

Returns parsed JSON dicts from Redis lists. Supports pagination via offset. Returns empty list if Redis is unavailable.

Full source: see `agent-core/tracing.py` in the repository.

---

## Step 2: Update FakeRedis for List Operations

### agent-core/tests/conftest.py

Add a `_lists` dict and four new methods to the `FakeRedis` class:

```python
def __init__(self):
    self._data = {}
    self._hashes = {}
    self._lists = {}      # NEW
    self._subscribers = {}
    self._lock = threading.Lock()

# -- List ops --
def lpush(self, name, *values):
    if name not in self._lists:
        self._lists[name] = []
    for v in values:
        self._lists[name].insert(0, v)
    return len(self._lists[name])

def ltrim(self, name, start, end):
    if name in self._lists:
        self._lists[name] = self._lists[name][start:end + 1]

def lrange(self, name, start, end):
    if name not in self._lists:
        return []
    if end == -1:
        return list(self._lists[name][start:])
    return list(self._lists[name][start:end + 1])

def llen(self, name):
    return len(self._lists.get(name, []))
```

Update `delete()` and `keys()` to include `_lists`:

```python
def delete(self, *keys):
    for k in keys:
        self._data.pop(k, None)
        self._hashes.pop(k, None)
        self._lists.pop(k, None)       # NEW

def keys(self, pattern="*"):
    import fnmatch
    all_keys = list(self._data.keys()) + list(self._hashes.keys()) + list(self._lists.keys())  # UPDATED
    return [k for k in all_keys if fnmatch.fnmatch(k, pattern)]
```

---

## Step 3: Wire Tracing into app.py

### Import and initialize

Add after existing imports:

```python
import tracing
```

Add after the Redis client setup:

```python
# Structured logging
tracing.setup_logging(redis_client=redis_client)
```

### Replace print statements in /chat

**Before (3 print statements):**
```python
print(f"[{request.channel}:{user_id}] {request.message}")
print(f"  -> Redis loaded {len(history)} messages ...")
print(f"  -> model: {model}  bootstrap: {in_bootstrap}")
```

**After (structured tracing):**
```python
trace_id = tracing.new_trace(user_id=user_id, channel=request.channel or "")

# After model routing:
tracing.log_chat_request(request.message, model=model, bootstrap=in_bootstrap)

# After Ollama response:
eval_count = response.get("eval_count", 0)
prompt_eval_count = response.get("prompt_eval_count", 0)
total_duration = response.get("total_duration", 0)
tracing.log_chat_response(
    model=model,
    response_preview=assistant_content,
    eval_count=eval_count,
    prompt_eval_count=prompt_eval_count,
    total_duration_ms=total_duration / 1_000_000 if total_duration else 0,
)
```

### Add trace_id to response

```python
return {"response": assistant_content, "model": model, "trace_id": trace_id}
```

---

## Step 4: Add Tracing Hooks to approval.py

### In create_request()

After the `redis.publish()` call, add:

```python
try:
    from tracing import log_approval_event
    log_approval_event(
        approval_id=approval_id,
        action=action,
        zone=zone,
        risk_level=risk_level,
        status="pending",
        description=description,
    )
except ImportError:
    pass
```

### In resolve()

After the `redis.hset()` call, add:

```python
try:
    from tracing import log_approval_event
    created_at = float(current.get("created_at", 0))
    response_time_ms = (resolved_at - created_at) * 1000 if created_at else 0
    log_approval_event(
        approval_id=approval_id,
        action=status,
        zone=current.get("zone", ""),
        risk_level=current.get("risk_level", ""),
        status=status,
        description=current.get("description", ""),
        response_time_ms=response_time_ms,
        resolved_by=resolved_by,
    )
except ImportError:
    pass
```

The lazy `from tracing import ...` inside a try/except means `approval.py` works independently even if `tracing.py` doesn't exist (useful for isolated testing).

---

## Step 5: Run the Tests

The full test suite runs without Docker.

### Install test dependencies (if not already installed)

```bash
cd agent-core
pip install pyyaml pytest pytest-asyncio
```

### Run all tests

```bash
python -m pytest tests/ -v
```

Expected output: **158 tests passed** in under 4 seconds.

### What the new tests cover

**test_tracing.py (49 tests across 10 classes):**

| Test Class | Count | What It Validates |
|-----------|-------|-------------------|
| TestTraceContext | 5 | `new_trace()` returns 16-char hex, sets context vars, successive traces differ, empty before first trace |
| TestJSONFormatter | 3 | Produces valid JSON, includes structured data, single-line output |
| TestChatLogging | 6 | Required fields present (timestamp, trace_id, user_id, channel, model), message truncated, response metrics, stored in Redis, in firehose, extra kwargs |
| TestSharedTraceID | 3 | Chat + skill share trace ID, chat + policy share trace ID, different traces get different IDs |
| TestSkillLogging | 2 | Skill fields present, stored in `logs:skill` |
| TestPolicyLogging | 3 | Policy fields present, stored in `logs:policy`, reason truncated |
| TestApprovalLogging | 4 | Requested event logged, resolved with response_time_ms, stored in `logs:approval`, no response_time when zero |
| TestRedisQueryable | 5 | `get_recent_logs()` returns entries, pagination with offset, firehose contains all types, handles None redis, handles empty list |
| TestRetention | 3 | `logs:all` trimmed to 1000, type lists trimmed to 500, firehose and type counts are independent |
| TestRedisResilience | 4 | Works without Redis (stdout only), survives Redis errors, returns logger, no duplicate handlers |
| TestSanitization | 11 | Redacts password/token/api_key/secret, nested redaction, case-insensitive, empty params safe, skill params sanitized in logs, truncation of long/short/non-strings |

### All existing tests still pass

The 109 existing tests (51 policy + 13 approval + 20 identity + 25 bootstrap) are unaffected. The only change to existing code is the tracing hooks in `approval.py`, which use lazy imports and don't alter behavior.

---

## Step 6: Rebuild and Verify

### Rebuild the stack

```bash
docker compose up --build -d
```

### Verify structured logging

```bash
# Send a chat message (X-Api-Key required — get value from .env)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $(grep AGENT_API_KEY .env | cut -d= -f2)" \
  -d '{"message": "hello", "user_id": "test", "channel": "curl"}'
```

The response now includes `trace_id`:
```json
{"response": "...", "model": "phi3:latest", "trace_id": "a1b2c3d4e5f67890"}
```

### Check structured logs in Docker

```bash
docker compose logs -f agent-core
```

You should see single-line JSON entries instead of the old `print()` output:
```json
{"timestamp": 1739836200.0, "level": "INFO", "message": "...", "event_type": "chat", "trace_id": "a1b2c3d4e5f67890", "user_id": "test", "channel": "curl", "model": "phi3:latest", "message_preview": "hello"}
```

### Query logs from Redis

```bash
# Connect to Redis CLI (Redis is password-protected — pass -a flag)
docker exec -it redis redis-cli -a $(grep REDIS_PASSWORD .env | cut -d= -f2)

# Check firehose
LRANGE logs:all 0 4

# Check chat-specific logs
LRANGE logs:chat 0 4

# Check list lengths
LLEN logs:all
LLEN logs:chat
```

### Verify approval tracing

Create an approval request (e.g., via bootstrap or the REST API) and check:

```bash
docker exec -it redis redis-cli -a $(grep REDIS_PASSWORD .env | cut -d= -f2) LRANGE logs:approval 0 4
```

You should see structured JSON entries for both the "pending" and "approved"/"denied" events, with `response_time_ms` on the resolution event.

---

## Log Entry Schema

Every log entry contains these common fields:

| Field | Type | Source |
|-------|------|--------|
| `event_type` | string | `"chat"`, `"skill"`, `"policy"`, or `"approval"` |
| `timestamp` | float | Unix epoch (seconds) |
| `trace_id` | string | 16-char hex, shared within a request |
| `user_id` | string | From `ChatRequest.user_id` |
| `channel` | string | From `ChatRequest.channel` |

Plus event-specific fields:

**Chat events:**
- `model`, `message_preview` (request)
- `model`, `response_preview`, `metrics.eval_count`, `metrics.prompt_eval_count`, `metrics.total_duration_ms` (response)

**Skill events:**
- `skill_name`, `params` (sanitized)

**Policy events:**
- `action`, `zone`, `decision`, `risk_level`, `reason`

**Approval events:**
- `approval_id`, `action`, `zone`, `risk_level`, `status`, `description`, `response_time_ms` (on resolution)

---

## Security Notes

- **Sensitive fields are redacted** — passwords, tokens, secrets, and API keys in skill parameters are replaced with `***REDACTED***` before logging. Case-insensitive matching.
- **Message content is truncated** — Chat messages are capped at 100 characters in logs to prevent sensitive content leaking. Response previews likewise truncated.
- **Redis failures don't crash requests** — All Redis writes are wrapped in try/except. If Redis goes down, structured logs still go to stdout.
- **No new dependencies** — Uses only Python stdlib modules. No additional pip packages required.
- **Approval tracing uses lazy imports** — `approval.py` imports tracing inside try/except, maintaining independence for testing.
- **CLI prints are preserved** — Only `app.py` print statements were replaced. `cli.py` user-facing output is unchanged.
- **Redis is password-protected** — All services connect via `redis://:${REDIS_PASSWORD}@redis:6379`. Set `REDIS_PASSWORD` in `.env`. Redis CLI commands require `-a <password>`.
- **`/chat` requires API key** — All requests to `POST /chat` must include `X-Api-Key: <AGENT_API_KEY>` header. The value is set in `.env` and injected into agent-core, telegram-gateway, and web-ui via docker-compose. Port 8000 is also bound to `127.0.0.1` only (not exposed to LAN).

---

## What's Next

This guide established the observability layer. The health dashboard (Chunk 3C) is now built on top of it:

1. **Health Dashboard (Chunk 3C)** ✅ — A separate Streamlit service (`dashboard/` on port 8502) that reads from these Redis log lists. Five panels: System Health (HTTP probes for all services), Activity (request counts, channel breakdown, response times), Queue & Jobs (placeholder + pending approvals), Recent Activity Feed (filterable log tail), and Security & Audit (policy denials + approval history). Auto-refreshes every 10 seconds. See `dashboard/app.py`, `dashboard/redis_queries.py`, and `dashboard/health_probes.py`.
2. **Skill Framework (Chunk 4A)** — When skills are added, `log_skill_call()` and `log_policy_decision()` will be wired into the skill execution pipeline, giving full visibility into every tool the agent uses
