# YouTube Video 3: Observability — Seeing Everything My AI Agent Does

## Video Title Options

- "I Can See Everything My AI Agent Does — Structured Tracing Tutorial"
- "Before Giving My Agent Tools, I Made It Transparent"
- "JSON Tracing for Self-Hosted AI Agents — Zero Dependencies"
- "My AI Agent Can't Hide Anything From Me — Observability from Scratch"

## Target Length: 18-24 minutes

---

## INTRO (2-3 min)

### Hook (0:00 - 0:45)
- Cold open: split screen. Left side: sending a chat message via curl. Right side: Redis CLI showing structured JSON logs appearing in real time — trace IDs, model names, token counts, durations.
- "Last video, we gave our agent guardrails and a soul. It can ask permission before doing anything dangerous. But right now, we're flying blind — the only logging is three print statements."
- "Today, we add structured tracing. Every request, every policy decision, every approval event — logged as JSON, stored in Redis, queryable by the dashboard we'll build next."

### What We're Adding (0:45 - 1:30)
- Show a simple before/after:
  ```
  BEFORE:
    print(f"[telegram:andy] hello")
    print(f"  -> model: phi3  bootstrap: False")

  AFTER:
    {"event_type": "chat", "trace_id": "a1b2c3d4e5f67890",
     "user_id": "andy", "channel": "telegram", "model": "phi3",
     "message_preview": "hello", "timestamp": 1739836200.0}
  ```
- Walk through the four things we're adding:
  1. **Trace IDs** — every request gets a unique ID, shared by all events in that request
  2. **JSON formatter** — structured logs to stdout for Docker
  3. **Redis storage** — dual-push to firehose + type-specific lists
  4. **Event emitters** — chat, skill, policy, approval — all traced

### Why Before Skills (1:30 - 2:15)
- "Next phase, we give the agent real tools — web search, file operations, code execution. Before that happens, we need to see what it's doing."
- "This is the same philosophy as building guardrails before the soul file: visibility before capability."
- "No new pip packages. No new containers. Just Python stdlib."

### What You'll Need (2:15 - 2:30)
- The working stack from Videos 1 & 2
- That's it. Zero new infrastructure.

---

## PART 1: THE PROBLEM WITH PRINT STATEMENTS (2-3 min)

### The Current State (2:30 - 3:30)
- Show the three print statements in `app.py`:
  ```python
  print(f"[{request.channel}:{user_id}] {request.message}")
  print(f"  -> Redis loaded {len(history)} messages ...")
  print(f"  -> model: {model}  bootstrap: {in_bootstrap}")
  ```
- "Three lines of logging for the entire agent. If something goes wrong, you're grepping through unstructured text."
- Send a message, show the Docker logs: plain text, no structure, no trace ID, no way to correlate events.

### What We Need (3:30 - 4:30)
- Show the list of questions we can't answer right now:
  - "Which model handled this request?"
  - "How long did Ollama take?"
  - "Was a policy check involved?"
  - "Did the approval gate fire? How long did the owner take to respond?"
  - "What's the request rate per channel?"
- "We need structured data, not print debugging."
- Show the target: a JSON log line with all fields, queryable from Redis

---

## PART 2: TRACE IDS WITH CONTEXTVARS (3-4 min)

### The Concept (4:30 - 5:30)
- Show a diagram:
  ```
  /chat request arrives
       |
  new_trace() --> generates "a1b2c3d4e5f67890"
       |
  log_chat_request()  --> trace_id: a1b2c3d4e5f67890
       |
  log_policy_decision() --> trace_id: a1b2c3d4e5f67890
       |
  log_approval_event()  --> trace_id: a1b2c3d4e5f67890
       |
  log_chat_response()   --> trace_id: a1b2c3d4e5f67890
  ```
- "One ID, set once, flows through everything. No passing `trace_id` as a parameter to every function."

### How contextvars Work (5:30 - 6:30)
- Show the code:
  ```python
  import contextvars

  _trace_id = contextvars.ContextVar("trace_id", default="")
  _user_id = contextvars.ContextVar("user_id", default="")
  _channel = contextvars.ContextVar("channel", default="")
  ```
- "Python's `contextvars` module was designed for exactly this. Each async request gets its own context. Set the trace ID once at the entry point, read it anywhere downstream."
- "No global state, no thread-local hacks, works perfectly with FastAPI's async request handling."

### Live Demo (6:30 - 7:30)
- Quick Python shell demo:
  ```python
  from tracing import new_trace, get_trace_id, get_trace_context

  tid = new_trace(user_id="andy", channel="telegram")
  print(tid)           # "a1b2c3d4e5f67890"
  print(get_trace_id())  # same thing
  print(get_trace_context())  # {trace_id, user_id, channel}

  # New trace replaces the old one
  tid2 = new_trace(user_id="bob", channel="cli")
  print(get_trace_id())  # different ID
  ```

---

## PART 3: THE JSON FORMATTER AND EVENT SYSTEM (3-4 min)

### JSONFormatter (7:30 - 8:30)
- Show the formatter class on screen — it's tiny:
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
- "Python's logging module does the heavy lifting. We just swap the formatter to output JSON instead of text."
- "Single-line JSON. Docker's log driver captures each line as a separate entry. Grep-friendly. Parseable."

### The _emit() Pipeline (8:30 - 9:30)
- Show the internal flow:
  ```
  log_chat_request("hello", model="phi3")
       |
  _emit("chat", {model: "phi3", message_preview: "hello"})
       |
  1. Build entry dict: event_type + timestamp + trace context + data
  2. JSON-serialize
  3. Log to stdout via JSONFormatter
  4. Push to Redis: logs:all + logs:chat
  5. LTRIM both lists (retention)
  6. Return the JSON string
  ```
- "Every emitter follows this exact pipeline. The only difference is the event type and the data fields."

### The Five Emitters (9:30 - 10:30)
- Flash each one on screen with its key fields:
  - `log_chat_request(message, model)` — message_preview, model
  - `log_chat_response(model, ..., eval_count, total_duration_ms)` — metrics dict
  - `log_skill_call(skill_name, params)` — sanitized params
  - `log_policy_decision(action, zone, decision, risk_level, reason)` — full policy result
  - `log_approval_event(approval_id, action, status, response_time_ms)` — approval lifecycle
- "Five functions. That's the entire public API. Everything else is internal."

---

## PART 4: REDIS STORAGE AND RETENTION (2-3 min)

### Dual-Push Strategy (10:30 - 11:30)
- Show the Redis key diagram:
  ```
  logs:all       [chat] [skill] [chat] [approval] [policy] [chat] ...  (max 1000)
  logs:chat      [chat] [chat] [chat] ...                               (max 500)
  logs:skill     [skill] [skill] ...                                     (max 500)
  logs:policy    [policy] [policy] ...                                   (max 500)
  logs:approval  [approval] [approval] ...                               (max 500)
  ```
- "Every event goes to two lists: the firehose (`logs:all`) and its type-specific list. The dashboard can show the activity feed from the firehose, or drill into just approvals, just policy decisions, etc."
- "Newest first — `LPUSH` puts new entries at the head."

### Count-Based Retention (11:30 - 12:00)
- "After every push, we `LTRIM` to keep the list at a fixed size. 1000 for the firehose, 500 per type."
- "No TTLs, no cron cleanup. Just a trim after every write. Simple, predictable memory usage."

### Resilience (12:00 - 12:30)
- Show the try/except in `_push_to_redis()`:
  ```python
  try:
      _redis_client.lpush("logs:all", json_str)
      _redis_client.ltrim("logs:all", 0, ALL_LOG_LIMIT - 1)
      # ... type-specific list
  except Exception:
      pass  # Never crash a request due to logging
  ```
- "If Redis goes down, the chat still works. Logs go to stdout. The agent doesn't crash because it couldn't write a log entry."

---

## PART 5: SANITIZATION AND SAFETY (2-3 min)

### Sensitive Data Redaction (12:30 - 13:30)
- Show the sanitization in action:
  ```python
  from tracing import _sanitize

  _sanitize({"url": "https://api.com", "token": "sk-abc123"})
  # -> {"url": "https://api.com", "token": "***REDACTED***"}

  _sanitize({"config": {"password": "secret", "host": "localhost"}})
  # -> {"config": {"password": "***REDACTED***", "host": "localhost"}}
  ```
- "Six sensitive key patterns: password, token, secret, api_key, apikey, api_secret. Case-insensitive. Nested dicts handled."
- "When skills are added, their parameters go through this before logging. API keys, tokens, passwords — never in the logs."

### Truncation (13:30 - 14:00)
- "Messages are truncated to 100 characters in logs. Responses too. Policy reasons capped at 200."
- "Prevents giant messages from bloating Redis. Prevents sensitive conversation content from leaking into logs."
- Show quick example:
  ```python
  _truncate("x" * 300)  # -> "xxxx...xxx..." (200 chars + "...")
  ```

---

## PART 6: WIRING IT INTO THE STACK (2-3 min)

### app.py Changes (14:00 - 15:00)
- Show the diff — what was added:
  ```python
  import tracing

  # At startup:
  tracing.setup_logging(redis_client=redis_client)

  # In /chat:
  trace_id = tracing.new_trace(user_id=user_id, channel=request.channel or "")
  tracing.log_chat_request(request.message, model=model, bootstrap=in_bootstrap)
  # ... after Ollama call ...
  tracing.log_chat_response(model=model, response_preview=assistant_content, ...)
  return {"response": assistant_content, "model": model, "trace_id": trace_id}
  ```
- "Three print statements removed. Four tracing calls added. The response now includes the trace ID."

### approval.py Changes (15:00 - 15:30)
- Show the lazy import pattern:
  ```python
  try:
      from tracing import log_approval_event
      log_approval_event(approval_id=approval_id, action=action, ...)
  except ImportError:
      pass
  ```
- "Lazy import with try/except. If tracing doesn't exist, approval.py still works. Good for isolated testing."
- "create_request() logs 'pending'. resolve() logs the resolution with response_time_ms."

---

## PART 7: LIVE DEMO (3-4 min)

### Send a Message, See the Trace (15:30 - 17:00)
- Terminal: send a curl request
  ```bash
  curl -s -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: $AGENT_API_KEY" \
    -d '{"message": "hello", "user_id": "andy", "channel": "demo"}' | jq
  ```
- Show the response with `trace_id` field
- Show Docker logs with structured JSON entries:
  ```bash
  docker compose logs --tail=5 agent-core
  ```
- Highlight: same trace_id on both the request and response log entries

### Query from Redis (17:00 - 18:00)
- Connect to Redis and query (Redis is password-protected):
  ```bash
  docker exec -it redis redis-cli -a $REDIS_PASSWORD
  > LRANGE logs:all 0 2
  > LRANGE logs:chat 0 2
  > LLEN logs:all
  ```
- "Same data, two views. The firehose has everything. The type list has just chat events."
- "This is what the dashboard will read. We haven't built it yet, but the data layer is ready."

### Trigger an Approval (18:00 - 19:00)
- If possible, trigger a bootstrap approval or use the REST API
- Show the approval events in `logs:approval`:
  ```bash
  docker exec -it redis redis-cli -a $REDIS_PASSWORD LRANGE logs:approval 0 4
  ```
- Point out `response_time_ms` on the resolution event — "how long did you take to tap Approve?"

---

## PART 8: RUNNING THE TESTS (1-2 min)

### Full Suite (19:00 - 20:00)
- Terminal recording:
  ```bash
  cd agent-core
  python -m pytest tests/ -v
  ```
- Show 158 tests passing — 109 existing + 49 new tracing tests
- Quick flash through test class names:
  - **TestTraceContext**: `test_new_trace_returns_16_char_hex`, `test_successive_traces_differ`
  - **TestChatLogging**: `test_log_chat_request_fields`, `test_log_chat_response_metrics`
  - **TestSharedTraceID**: `test_chat_and_skill_share_trace_id`
  - **TestRedisResilience**: `test_logging_works_without_redis`, `test_logging_survives_redis_error`
  - **TestSanitization**: `test_redact_password`, `test_skill_params_sanitized`
  - **TestRetention**: `test_all_log_trimmed_to_limit`
- "49 new tests. Zero regressions on the existing 109. All passing in under 4 seconds."

---

## OUTRO (1-2 min)

### Recap (20:00 - 21:00)
- Quick visual summary — what we added:
  - **Trace IDs** via contextvars — one ID per request, shared by all events
  - **JSON formatter** — structured logs replace print statements
  - **Redis dual-push** — firehose + type lists with count-based retention
  - **Five emitters** — chat, skill, policy, approval events
  - **Sanitization** — sensitive keys redacted, fields truncated
  - **Resilience** — Redis down? Stdout still works. Tracing never crashes a request.
- "Zero new dependencies. Zero new containers. Just 240 lines of Python stdlib."

### What's Next (21:00 - 22:00)
- "The health dashboard is already built — it's a separate Streamlit app on port 8502 that reads from these exact Redis lists. Five panels: service health, activity metrics, pending approvals, a filterable activity feed, and a security audit trail. Auto-refreshes every 10 seconds."
- "Next up: the skill framework. When the agent starts calling tools — web search, file operations, code execution — every single call flows through this tracing pipeline."
- "You'll see exactly what the agent did, what it was allowed to do, what it was denied, and how long it took."
- "Subscribe so you don't miss it."

### Call to Action (22:00 - 22:30)
- "All the code is linked below, including the full test suite and the dashboard."
- "If you're building your own agent stack and have questions about the tracing setup, drop a comment."
- Like/subscribe/etc.

---

## PRODUCTION NOTES

### B-Roll / Visuals Needed
- Before/after comparison: print statements vs. structured JSON
- Trace ID flow diagram (single ID flowing through multiple function calls)
- Redis key structure diagram (firehose + type lists with retention arrows)
- Docker logs terminal recording showing JSON output
- Redis CLI terminal recording showing LRANGE queries
- Test suite passing — 158 green checkmarks

### Key Demo Moments (Get These Right)
1. **The before/after** (Part 1) — print statement output vs. structured JSON
2. **Trace ID correlation** (Part 7) — same trace_id appearing on request + response events
3. **Redis query** (Part 7) — querying logs from Redis CLI, showing structured data
4. **Resilience test** (Part 4) — mention that Redis going down doesn't break the agent
5. **Test count** (Part 8) — 158 tests, 49 new, zero regressions

### Editing Notes
- This is a shorter, more technical video than Video 2 — keep the pace up
- Use split screen for the live demo: curl/logs on left, Redis CLI on right
- Flash through test output quickly — focus on the count, not individual test names
- Consider a brief animation showing events flowing from /chat through the trace pipeline to Redis lists
- Add chapter markers matching sections above
- Lower-third labels for code files ("agent-core/tracing.py", "agent-core/app.py")

### Thumbnail Ideas
- Terminal showing structured JSON with trace_id highlighted in green
- "See Everything" text with magnifying glass over JSON log output
- Split: messy print statements (red X) vs. clean JSON (green check)
- Redis CLI showing structured log entries with "Observability" overlay text

### Description Template
```
Before giving my AI agent real tools, I made it completely
transparent. Structured JSON tracing for every request, every
policy decision, every approval event — with zero new dependencies.

Part 1 (build the stack): [VIDEO_1_LINK]
Part 2 (guardrails & soul): [VIDEO_2_LINK]
Code & Setup Guide: [GITHUB_LINK]

TIMESTAMPS:
0:00 - Intro
2:30 - Part 1: The Problem with Print Statements
4:30 - Part 2: Trace IDs with contextvars
7:30 - Part 3: JSON Formatter & Event System
10:30 - Part 4: Redis Storage & Retention
12:30 - Part 5: Sanitization & Safety
14:00 - Part 6: Wiring It into the Stack
15:30 - Part 7: Live Demo
19:00 - Part 8: Running the Tests
20:00 - Recap & What's Next

WHAT WAS ADDED:
- tracing.py — 240 lines, Python stdlib only
- contextvars for per-request trace IDs
- JSONFormatter for structured stdout logging
- Redis dual-push (logs:all + logs:chat/skill/policy/approval)
- Count-based retention (LTRIM after every push)
- Sensitive data redaction (password, token, secret, api_key)
- 49 new tests (158 total, zero regressions)

TECH STACK:
- Everything from Videos 1 & 2
- Python stdlib: logging, contextvars, json, uuid, time
- No new pip packages or containers

#AI #AIAgent #Observability #Tracing #SelfHosted #Ollama #Docker #Tutorial
```
