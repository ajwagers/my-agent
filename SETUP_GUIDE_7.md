# SETUP_GUIDE_7 — Voice Gateway + Tool Calling Reliability

**Phase:** Phase 5 polish + stability fixes
**Prerequisite:** SETUP_GUIDE_6 complete (Phase 4D + Brave Search, 471 tests passing), Mumble bot running
**Goal:** Make Mumble voice chat work end-to-end; fix tool calling reliability and async hang issues.

This guide covers three independent fix sets, all applied together:

1. **Mumble voice quality** — TTS-safe response stripping, voice-concise system prompt, PTT-flush worker
2. **qwen3:8b tool calling** — `think=False` suppression, broadened refusal/signal detection, stronger search trust
3. **Async client migration** — replace sync `ollama.Client` with `AsyncClient` to eliminate hung connections

---

## External Accounts and Software You Need

These must be set up on your own — none require payment at normal personal-agent usage volumes.

### Mumble Client (required to test voice chat)

The Mumble server runs in Docker. You need a client to connect:

| Platform | Recommended Client | Download |
|---|---|---|
| Windows / macOS / Linux | **Mumble** (official) | mumble.info |
| Android | **Mumla** | F-Droid or Google Play Store |
| iOS | **Mumble for iOS** | App Store |

Connection settings:
- **Server:** `localhost` (or LAN IP if connecting from a mobile device on the same network)
- **Port:** `64738`
- **Password:** your `MUMBLE_SERVER_PASSWORD` from `.env` (leave blank if you didn't set one)
- **Username:** anything — the bot uses it as `user_id`

No account registration required. Mumble generates a certificate automatically on first connect.

### Brave Search API Key (primary search backend)

1. Go to **search.brave.com/search/api** and create an account
2. Subscribe to the **Data for AI** plan (free tier: $5/month credit ≈ 1,000 queries/month)
3. Generate an API key (starts with `BSA`)
4. Add to `.env`:
   ```
   BRAVE_SEARCH_API_KEY=BSAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Tavily API Key (optional fallback)

1. Go to **tavily.com** and create an account
2. Free tier: 1,000 searches/month
3. Add to `.env`:
   ```
   TAVILY_API_KEY=tvly-dev-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

> If only `BRAVE_SEARCH_API_KEY` is set, Tavily fallback skips gracefully.
> If neither is set, the agent returns a clear error message instead of hallucinating.

### `.env` additions for Mumble (from Phase 5, if not already done)

```bash
MUMBLE_SUPERUSER_PASSWORD=changeme_strong_password   # Murmur admin (SuperUser) password
MUMBLE_SERVER_PASSWORD=                              # optional: require a password to join
```

---

## What Changes

| File | Change |
|---|---|
| `mumble-bot/bot.py` | `_strip_for_speech()`, `_call_agent_with_progress()`, `vad_flush_worker()` |
| `agent-core/requirements.txt` | `ollama>=0.4.7` (was pinned at 0.3.3) |
| `agent-core/skill_runner.py` | `think=False`, `AsyncClient` API compat helpers, broader patterns |
| `agent-core/app.py` | `AsyncClient`, voice system prompt, broader `_SIGNAL_REALTIME`, search trust |

---

## Part 1: Mumble Voice Quality

### Problem
The bot was calling `_call_agent()` and sending raw markdown to TTS — code blocks, bullet points, headers. Piper TTS would literally read "asterisk asterisk bold asterisk asterisk" aloud. Responses were also often verbose, unsuited for speech.

### Fix 1a: Strip markdown before TTS

In `mumble-bot/bot.py`, add `_strip_for_speech()` and call it before synthesis:

```python
def _strip_for_speech(text: str) -> str:
    """Strip markdown/HTML so TTS produces clean spoken prose."""
    # Remove fenced code blocks entirely — not useful when spoken
    text = re.sub(r"```[\s\S]*?```", "code block omitted.", text)
    # Inline code — just keep the content
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold / italic markers
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    # Markdown headers → plain text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bullet / numbered list items — strip the marker
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

In `queue_worker`, use it before TTS:

```python
elif job["type"] == "voice":
    ...
    response = _call_agent_with_progress(transcript, job["username"])
    mumble.my_channel().send_text_message(response)
    speech_text = _strip_for_speech(response)          # ← strip before TTS
    print(f"[TTS] synthesizing response ({len(speech_text)} chars)", flush=True)
    _play_and_wait(tts, speech_text)
    print(f"[TTS] playback complete", flush=True)
```

### Fix 1b: Voice system prompt in agent-core

In `agent-core/app.py`, after the tool-forcing directive block, add:

```python
# Voice channel: ask for short, spoken-language responses
if request.channel == "mumble":
    system_prompt += (
        "\n\n## Voice Response Guidelines\n"
        "Your response will be read aloud via text-to-speech. "
        "Be concise — 1 to 4 sentences unless the question genuinely needs more. "
        "Use plain spoken prose: no markdown, no bullet points, no headers, no code fences. "
        "If you must list items, connect them naturally with words like 'and' or 'then'. "
        "Avoid starting with filler phrases like 'Certainly!' or 'Of course!'."
    )
```

This is injected after `_tool_forcing_directive` so it can override any markdown-heavy guidance.

### Fix 1c: Progress ticks + PTT-flush worker

Replace `_call_agent()` with a version that sends periodic "still working..." ticks, spacing them out as the request runs longer:

```python
PROGRESS_INTERVALS = [30, 60, 90]  # last value repeats

def _call_agent_with_progress(message: str, user_id: str) -> str:
    """Call agent and send periodic progress ticks, spacing out over time."""
    stop_event = threading.Event()

    def _reporter():
        elapsed = 0
        idx = 0
        while True:
            interval = PROGRESS_INTERVALS[min(idx, len(PROGRESS_INTERVALS) - 1)]
            if stop_event.wait(timeout=interval):
                break
            elapsed += interval
            idx += 1
            try:
                mumble.my_channel().send_text_message(f"<i>⏳ Still working... ({elapsed}s)</i>")
            except Exception:
                pass

    t = threading.Thread(target=_reporter, daemon=True)
    t.start()
    try:
        return _call_agent(message, user_id)
    finally:
        stop_event.set()
        t.join(timeout=1)
```

Add a `vad_flush_worker` to handle PTT release (push-to-talk: user stops transmitting mid-utterance):

```python
def vad_flush_worker():
    """Periodically flush VAD buffers for users who stopped transmitting (PTT released)."""
    while True:
        time.sleep(0.3)
        try:
            for username, utterance in vad_tracker.flush_stale():
                print(f"[VAD] flush (stream ended) from {username}: {len(utterance)/1920*20:.0f}ms", flush=True)
                _push_voice_job(username, 0, utterance)
        except Exception as e:
            print(f"VAD flush error: {e}", flush=True)
```

Start it in `main()`:

```python
threading.Thread(target=vad_flush_worker, daemon=True).start()
```

---

## Part 2: qwen3:8b Tool Calling Reliability

### Problem 1: Model answers from training data instead of calling `web_search`

qwen3:8b has a thinking mode (`<think>...</think>` tokens) that, when enabled, causes the model to reason itself out of tool use: "I know this from training data, so I don't need to search." The result: `tool_iterations: 0, skills_called: []` even when the question clearly needs a search.

### Fix: `think=False` in tool-dispatch calls

Requires `ollama>=0.4.7` (see Part 3). In `agent-core/skill_runner.py`, pass `think=False` to all tool-dispatch `chat()` calls inside `run_tool_loop`:

```python
response = await ollama_client.chat(
    model=model,
    messages=messages,
    tools=tools,
    options=options,
    think=False,         # ← suppresses qwen3 extended reasoning during tool dispatch
)
```

This only applies to the tool-calling loop. The final synthesis call (when max iterations reached) also passes `think=False`. Pure-chat paths (no tools) go through without the flag.

### Problem 2: "Who is the President?" not triggering a search

`_REALTIME_SIGNAL` and `_SIGNAL_REALTIME` (in skill_runner.py and app.py respectively) didn't match questions about current office-holders. The nudge retry and upfront tool-forcing directive never fired.

### Fix: Broader signal patterns

In **`agent-core/skill_runner.py`**, extend `_REALTIME_SIGNAL`:

```python
_REALTIME_SIGNAL = re.compile(
    r"current|latest|recent|today|tonight|right now|live|"
    r"weather|forecast|temperature|"
    r"price|stock|crypto|bitcoin|"
    r"score|result|standings|match|game|"
    r"news|breaking|headline|"
    r"scrape|crawl|fetch.+url|"
    r"search for|look up|find out|check if|"
    r"who won|what happened|is .{1,30} open|when does|"
    # Current office-holders, leadership, status questions
    r"who is (?:the |a )?(?:current )?(?:president|prime minister|ceo|head|"
    r"leader|governor|mayor|secretary|director|chancellor|king|queen|pope)|"
    r"who (?:leads|runs|heads|controls|governs|commands)\b|"
    r"who is in (?:charge|office|power)\b|"
    r"what is the (?:current |latest )?(?:status|state|situation|rate|level)\b|"
    r"is .{1,40} still\b|"
    r"has .{1,40} (?:changed|updated|happened)\b",
    re.IGNORECASE,
)
```

Apply the **same additions** to `_SIGNAL_REALTIME` in **`agent-core/app.py`** (used for the upfront forcing directive).

### Problem 3: Model calls `web_search`, gets correct results, dismisses them as "fictional"

qwen3:8b's training priors are strong enough that, when results say "Donald Trump is the 47th president" but the model was trained during Biden's term, it sometimes dismisses the results as a "hypothetical scenario."

### Fix: Broadened `_REFUSAL_PATTERN` + stronger system prompt trust language

In **`agent-core/skill_runner.py`**, extend `_REFUSAL_PATTERN` to catch confident-but-stale answers:

```python
_REFUSAL_PATTERN = re.compile(
    r"don.t have real.time"
    r"|real.time capabilities"
    r"|real.time access"
    r"|training data"
    r"|knowledge cutoff"
    r"|can.t access the internet"
    r"|cannot access the internet"
    r"|no internet access"
    r"|not able to browse"
    r"|cannot browse"
    r"|don.t have access to current"
    r"|web.scrap"
    r"|cannot fetch"
    r"|can.t fetch"
    r"|unable to fetch"
    r"|api access"
    r"|cannot.*external"
    r"|unable to access"
    # Models that answer confidently from stale training data
    r"|developed prior to"
    r"|prior to \w+ 20\d\d"
    r"|as of my (training|knowledge|last)"
    r"|my (training|knowledge) (cutoff|through|until|ends)"
    r"|after my (last|latest) update"
    r"|last updated? (in |on )?\w* ?20\d\d"
    r"|information.*(?:through|until|up to).*20\d\d"
    r"|I (?:was|am) (?:an AI|a language model).{0,60}(?:20\d\d|cutoff|training)",
    re.IGNORECASE,
)
```

In **`agent-core/app.py`**, strengthen the search trust language in the tool usage block:

```python
"- When search results are returned, base your answer ONLY on those results. "
"Search results reflect the real world RIGHT NOW and are ALWAYS more accurate "
"than your training data about current events, people, or facts. "
"NEVER dismiss search results as fictional, hypothetical, or inconsistent with "
"your training — your training is outdated, the search results are not. "
"If search results say X is president, CEO, or any office-holder, that IS the "
"correct current answer regardless of what you learned during training."
```

---

## Part 3: Async Client Migration

### Problem: Hung connections (6+ minute stall)

The `_summarise_and_store` background task used `asyncio.to_thread(sync_client.chat, ...)` — the sync `ollama.Client` wraps `httpx` with a shared connection pool. When called from a thread pool via `asyncio.to_thread`, `httpx` connections can silently drop with no timeout enforcement. The result: the thread hangs indefinitely (confirmed via `/proc/[pid]/task/*/wchan` showing thread 36 stuck in `wait_woken`, Ollama GIN logs showing no active requests).

### Fix: Switch to `AsyncClient` throughout

#### Step 1: Update `requirements.txt`

```
ollama>=0.4.7  # Ollama Python client (0.4.7+ required for think=False)
```

(Remove the old `ollama==0.3.3` pin.)

#### Step 2: Update `agent-core/app.py`

```python
from ollama import AsyncClient

ollama_client = AsyncClient(host=OLLAMA_HOST, timeout=300)
```

The `timeout=300` is now enforced by the async HTTP client — if Ollama doesn't respond in 5 minutes, a `TimeoutException` is raised immediately instead of hanging.

Fix `_summarise_and_store` to use `await` directly (no thread wrapper needed):

```python
async def _summarise_and_store(dropped: list, user_id: str) -> None:
    try:
        ...
        response = await ollama_client.chat(          # ← no asyncio.to_thread
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            options={"num_ctx": 2048},
        )
        summary = (response.message.content or "").strip()  # ← attribute access
        ...
    except Exception:
        pass
```

#### Step 3: Update `agent-core/skill_runner.py`

The ollama 0.6.1 library changed response objects from plain dicts to typed objects. All dict-style access breaks. Add two helpers inside `run_tool_loop`:

```python
def _msg_content(msg) -> str:
    """Extract text content from a Message object or dict."""
    if isinstance(msg, dict):
        return msg.get("content", "") or ""
    return msg.content or ""

def _msg_to_dict(msg) -> Dict:
    """Convert an ollama Message object to a plain dict for the messages list."""
    if isinstance(msg, dict):
        return msg
    d: Dict = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return d
```

Update all response access throughout `run_tool_loop`:

| Old (0.3.3 dict) | New (0.6.1 attribute) |
|---|---|
| `response["message"]` | `response.message` |
| `msg.get("tool_calls")` | `msg.tool_calls` |
| `msg.get("content", "")` | `_msg_content(msg)` |
| `{**msg, "role": "assistant"}` | `_msg_to_dict(msg)` |
| `tc["function"]["name"]` | `tc.function.name` |
| `tc["function"]["arguments"]` | `tc.function.arguments` |

Remove `import asyncio` from skill_runner.py (no longer used).

All three `asyncio.to_thread(ollama_client.chat, ...)` calls become plain `await ollama_client.chat(...)`:

```python
# No tools — straight to model
response = await ollama_client.chat(model=model, messages=messages, options=options)

# Tool loop
response = await ollama_client.chat(
    model=model,
    messages=messages,
    tools=tools,
    options=options,
    think=False,
)

# Max iterations synthesis
response = await ollama_client.chat(model=model, messages=messages, options=options)
```

---

## Build and Deploy

All three changes require a rebuild of agent-core (source is copied into the image at build time):

```bash
docker compose build agent-core
docker compose up -d agent-core

# Watch startup
docker compose logs -f agent-core
# Expect: "Application startup complete."

# For mumble-bot changes only:
docker compose build mumble-bot
docker compose up -d mumble-bot
docker compose logs -f mumble-bot
# Expect: "Connected to mumble-server:64738 as Agent"
```

---

## Verification

### Tool calling smoke tests

```bash
# Should call web_search (matches _SIGNAL_REALTIME "who is the president")
agent chat "who is the president of the united states?"
# → Should return Trump (as of 2025) with source from web_search, not training data

# Should call web_search (matches "latest")
agent chat "what is the latest version of Python?"

# Should call web_search (matches "is X still")
agent chat "is Elon Musk still the CEO of Tesla?"

# Should NOT stall — async client timeout enforced at 300s
agent chat "please recite the first verse of a Shakespeare sonnet"
```

### Mumble voice tests

1. Connect via any Mumble client to `mumble://localhost:64738`
2. Type "what time is it" — agent replies as text (no markdown artifacts)
3. Speak into mic — transcript appears in italics, agent replies in text + speaks response
4. Ask "who is the president?" — agent calls web_search, gives spoken answer without markdown
5. For a long request, progress ticks appear at 30s, 90s, 180s intervals

### Check for hung-connection fix

```bash
# Request that previously caused a 6-minute stall
agent chat "recite Shakespeare sonnet 18"
# → Should complete in ~10-30s with a spoken response, never stall
```

---

## Why Each Change Was Necessary

| Problem observed | Root cause | Fix applied |
|---|---|---|
| TTS reading asterisks and hashes aloud | Raw markdown sent to Piper TTS | `_strip_for_speech()` strips all markdown before synthesis |
| Long verbose spoken responses | No voice-specific system prompt | `channel == "mumble"` check appends concise-prose guidelines |
| PTT user stops talking, utterance never processed | VAD only emits on silence timeout, not stream-end | `vad_flush_worker()` polls and flushes stale buffers every 300ms |
| "Who is the president?" → training data answer | `_REALTIME_SIGNAL` didn't match political queries | Added office-holder patterns to both signal regexes |
| Model calls search, dismisses results as fictional | qwen3 training priors override correct search data | Broader `_REFUSAL_PATTERN` + "NEVER dismiss search results" in prompt |
| No tool calls at all despite real-time query | qwen3 thinking mode reasons itself out of tool use | `think=False` parameter (requires ollama ≥0.4.7) |
| 6-minute hang, no response | `asyncio.to_thread(sync_client.chat)` + httpx connection pool drop | `AsyncClient` with `timeout=300` enforces timeouts, no thread wrapping |
| `'Message' object is not a mapping` | ollama 0.6.1 changed from dict to typed objects | `_msg_content()` and `_msg_to_dict()` helpers; attribute access throughout |
