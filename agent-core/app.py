from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from ollama import AsyncClient
import asyncio
import os
import json
import re
import time
import redis
from datetime import datetime, timezone

from policy import PolicyEngine
from approval import ApprovalManager
from approval_endpoints import router as approval_router
from job_endpoints import router as jobs_router
import identity as identity_module
import bootstrap
import tracing
from memory import MemoryStore
from heartbeat import start_heartbeat
from job_manager import JobManager
from skills.registry import SkillRegistry
from skills.rag_ingest import RagIngestSkill
from skills.rag_search import RagSearchSkill
from skills.web_search import WebSearchSkill
from skills.file_read import FileReadSkill
from skills.file_write import FileWriteSkill
from skills.url_fetch import UrlFetchSkill
from skills.pdf_parse import PdfParseSkill
from skills.remember import RememberSkill
from skills.recall import RecallSkill
from skills.create_task import CreateTaskSkill
from skills.list_tasks import ListTasksSkill
from skills.cancel_task import CancelTaskSkill
from skills.calculate import CalculateSkill
from skills.convert_units import ConvertUnitsSkill
from skills.python_exec import PythonExecSkill
from skills.calendar_read import CalendarReadSkill
from skills.calendar_write import CalendarWriteSkill
from skills.memory_capture import MemoryCaptureSkill
from skills.memory_search import MemorySearchSkill
from skills.sp_inventory import SummitPineInventorySkill
from skills.sp_orders import SummitPineOrdersSkill
from skills.sp_faq import SummitPineFAQSkill
from skill_runner import run_tool_loop
from memory_middleware import build_brain_context

app = FastAPI()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
REASONING_MODEL = os.getenv("REASONING_MODEL", "qwen3:8b")
ollama_client = AsyncClient(host=OLLAMA_HOST, timeout=300)

# API key auth for /chat
_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)

def _require_api_key(api_key: str = Security(_api_key_header)):
    expected = os.getenv("AGENT_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="AGENT_API_KEY not configured on server")
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Structured logging
tracing.setup_logging(redis_client=redis_client)

# Policy engine & approval manager
policy_engine = PolicyEngine(config_path="policy.yaml", redis_client=redis_client)
approval_manager = ApprovalManager(redis_client=redis_client)
app.state.policy_engine = policy_engine
app.state.approval_manager = approval_manager
app.state.redis_client = redis_client

# Approval REST endpoints
app.include_router(approval_router)
app.include_router(jobs_router)

# Skill registry
skill_registry = SkillRegistry()
skill_registry.register(RagIngestSkill())
skill_registry.register(RagSearchSkill())
skill_registry.register(WebSearchSkill())
skill_registry.register(FileReadSkill())
skill_registry.register(FileWriteSkill())
skill_registry.register(UrlFetchSkill())
skill_registry.register(PdfParseSkill())
skill_registry.register(RememberSkill())
skill_registry.register(RecallSkill())
skill_registry.register(CreateTaskSkill(redis_client))
skill_registry.register(ListTasksSkill(redis_client))
skill_registry.register(CancelTaskSkill(redis_client))
skill_registry.register(CalculateSkill())
skill_registry.register(ConvertUnitsSkill())
skill_registry.register(PythonExecSkill(ollama_host=OLLAMA_HOST, reasoning_model=REASONING_MODEL))
skill_registry.register(CalendarReadSkill())
skill_registry.register(CalendarWriteSkill())
skill_registry.register(MemoryCaptureSkill())
skill_registry.register(MemorySearchSkill())
skill_registry.register(SummitPineInventorySkill())
skill_registry.register(SummitPineOrdersSkill())
skill_registry.register(SummitPineFAQSkill())

# Long-term memory singleton — used for working memory injection in system prompt
memory_store = MemoryStore()

# Config
HISTORY_TOKEN_BUDGET = int(os.getenv("HISTORY_TOKEN_BUDGET", "6000"))
NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "phi4-mini:latest")
DEEP_MODEL = os.getenv("DEEP_MODEL", "qwen2.5:14b")
DEEP_NUM_CTX = int(os.getenv("DEEP_NUM_CTX", "32768"))
CODING_MODEL = os.getenv("CODING_MODEL", "qwen3:8b")
REASONING_KEYWORDS = [
    "explain", "analyze", "plan", "why", "compare",
    "reason", "think", "step by step", "how does", "what if",
]
CODING_KEYWORDS = [
    "code", "debug", "implement", "refactor", "function", "class",
    "script", "bug", "fix", "test", "write a program", "write a script",
    "write a function", "write a class", "write a test", "unit test",
]
TOOL_MODEL = os.getenv("TOOL_MODEL", "qwen3:8b")
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "5"))

def estimate_tokens(text):
    """Rough chars-to-tokens heuristic."""
    return len(text) // 4


def _format_age(seconds: float) -> str:
    """Format elapsed seconds into a compact human-readable age string."""
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d"
    weeks = days / 7
    if weeks < 4.3:
        return f"{int(weeks)}w"
    months = days / 30
    return f"{int(months)}mo"


def build_working_memory(user_id: str) -> str:
    """Build a compact working memory block for injection into the system prompt.

    Returns empty string if ChromaDB is unavailable or no memories exist.
    Hard cap: 1200 chars (~300 tokens).
    """
    try:
        entries = memory_store.get_recent(user_id, n=8)
    except Exception:
        return ""
    if not entries:
        return ""
    now = time.time()
    lines = []
    for entry in entries:
        memory_type = entry.get("type", "fact")
        content = entry.get("content", "")
        timestamp = entry.get("timestamp", now)
        age = _format_age(now - timestamp)
        lines.append(f"- [{memory_type}] {content} ({age})")
    block = "## Working Memory\n" + "\n".join(lines)
    if len(block) > 1200:
        block = block[:1197] + "[...]"
    return block


async def _summarise_and_store(dropped: list, user_id: str) -> None:
    """Summarise dropped history messages and store to long-term memory.

    Fire-and-forget — never raises; all errors are silently swallowed.
    """
    try:
        text = "\n".join(
            f"{m['role'].upper()}: {m.get('content', '')[:400]}"
            for m in dropped
        )
        summary_prompt = (
            "Summarise the following conversation excerpt in 2-3 sentences. "
            "Focus on facts, preferences, and important context:\n\n" + text
        )
        response = await ollama_client.chat(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            options={"num_ctx": 2048},
        )
        summary = (response.message.content or "").strip()
        if summary:
            memory_store.add(summary, "summary", user_id, source="agent")
    except Exception:
        pass

class ChatRequest(BaseModel):
    message: str
    model: str = None  # None = auto-route
    user_id: str = None
    channel: str = None
    auto_approve: bool = False
    history: list = None  # Optional: client-provided conversation history

# ---------------------------------------------------------------------------
# Per-request tool-forcing signals
#
# Checked against the user's message BEFORE the first LLM call.  When a
# signal matches, a hard directive is appended to the system prompt so the
# model is told explicitly which tool to call — rather than relying solely
# on the general tool-usage guidance or post-hoc nudging.
#
# Keep patterns specific enough to avoid false positives on casual phrasing.
# ---------------------------------------------------------------------------

_SIGNAL_URL = re.compile(r"https?://\S+", re.IGNORECASE)

_SIGNAL_REALTIME = re.compile(
    r"current|latest|recent|today|tonight|right now|live\b|"
    r"weather|forecast|temperature|"
    r"price|stock|crypto|bitcoin|"
    r"score|result|standings|match\b|game\b|"
    r"news|breaking|headline|"
    r"scrape|crawl\b|"
    r"search for|look up|find out|check if|"
    r"who won|what happened|is .{1,30} open|when does|"
    r"who is (?:the |a )?(?:current )?(?:president|prime minister|ceo|head|"
    r"leader|governor|mayor|secretary|director|chancellor|king|queen|pope)|"
    r"who (?:leads|runs|heads|controls|governs)\b|"
    r"who is in (?:charge|office|power)\b|"
    r"is .{1,40} still\b",
    re.IGNORECASE,
)

_SIGNAL_RECALL = re.compile(
    r"do you remember|you(?:'ve| have) (?:stored|saved|remembered)|"
    r"what did i (?:say|tell you|mention)|"
    r"my (?:name|preference|email|phone|address|location)\b|"
    r"from (?:last|our previous|a previous) (?:session|conversation|time)|"
    r"have i (?:told|mentioned|said)",
    re.IGNORECASE,
)

_SIGNAL_FILE = re.compile(r"/sandbox/\S+|/agent/\S+|/app/\S+", re.IGNORECASE)

_SIGNAL_SCHEDULE = re.compile(
    r"schedule|remind me|every day|every hour|every week|recurring|"
    r"run this later|set up a job|create a task|in \d+\s*(minute|hour|day|week)",
    re.IGNORECASE,
)

_SIGNAL_CALCULATE = re.compile(
    r"\bcalculate\b|\bcompute\b|\bevaluate\b|\bsolve\b|"
    r"what is \d|\d+\s*[\+\-\*\/\^]\s*\d|"
    r"\bsqrt\b|\bsin\b|\bcos\b|\blog\b|\bfactorial\b",
    re.IGNORECASE,
)

_SIGNAL_CONVERT = re.compile(
    r"\bconvert\b.{0,40}\bto\b|"
    r"how many (km|miles?|kg|lbs?|pounds?|feet|meters?|gallons?|liters?)\b|"
    r"\bin (kilometers?|miles?|celsius|fahrenheit|kg|pounds?|lbs?|meters?|feet|mph|kph)\b",
    re.IGNORECASE,
)

_SIGNAL_PYTHON = re.compile(
    r"\brun\s+(this\s+)?code\b|\bexecute\s+(this\s+)?script\b|"
    r"\bpython\s+script\b|\brun\s+python\b|"
    r"```python",
    re.IGNORECASE,
)

_SIGNAL_BRAIN_CAPTURE = re.compile(
    r"^/remember\b|^remember\s*:\s*|^remember this\b",
    re.IGNORECASE,
)

_SIGNAL_BRAIN_SEARCH = re.compile(
    r"do you remember|what do you know about|have i (?:told|mentioned)|"
    r"recall|from (?:last|our previous)|what did i say",
    re.IGNORECASE,
)

_SIGNAL_INVENTORY = re.compile(
    r"\bstock\b|\binventory\b|\blow.?stock\b|\breorder\b|\bbatch\b|"
    r"\bcuring\b|\bSP-[A-Z]+\b",
    re.IGNORECASE,
)

_SIGNAL_FAQ = re.compile(
    r"\bguarantee\b|\brefund\b|\bhow to use\b|\bhow long\b|"
    r"\bingredient\b|\bcustomer\b|\bwhat.s in\b",
    re.IGNORECASE,
)

_SIGNAL_CALENDAR = re.compile(
    r"\b(what'?s?\s+on\s+my\s+calendar|am\s+i\s+free|upcoming\s+events?|"
    r"schedule\s+a\s+meeting|add\s+(an?\s+)?event|create\s+(an?\s+)?meeting|"
    r"my\s+calendar|calendar\s+this\s+week)\b",
    re.IGNORECASE,
)


def _tool_forcing_directive(message: str) -> str:
    """Return a system-prompt suffix that forces the right tool(s) for this
    request, or an empty string if no signals are detected.

    Injected at the END of the system prompt so it acts as a per-request
    override on top of the general tool-usage guidance.
    """
    directives = []

    if _SIGNAL_URL.search(message):
        directives.append(
            "The user's message contains a URL. You **must** call `url_fetch` "
            "on that URL to retrieve its actual content before responding. "
            "Do not guess or describe it from training data."
        )

    if _SIGNAL_REALTIME.search(message):
        directives.append(
            "This question requires current information. You **must** call "
            "`web_search` before answering. Do not answer from training data."
        )

    if _SIGNAL_RECALL.search(message):
        directives.append(
            "The user may be referencing something from a previous conversation. "
            "You **must** call `recall` to check long-term memory before answering."
        )

    if _SIGNAL_FILE.search(message):
        directives.append(
            "The user's message references a file path. You **must** call "
            "`file_read` on that path before responding. "
            "Do not guess the file contents."
        )

    if _SIGNAL_SCHEDULE.search(message):
        directives.append(
            "The user wants to schedule or create a recurring task. "
            "You **must** call `create_task` to register the job. "
            "Do not just describe what you would do — actually call the tool."
        )

    if _SIGNAL_CALCULATE.search(message):
        directives.append(
            "The user is asking for a mathematical calculation. "
            "You **must** call `calculate` with the expression. "
            "Do not compute math in your head — use the tool."
        )

    if _SIGNAL_CONVERT.search(message):
        directives.append(
            "The user is asking for a unit conversion. "
            "You **must** call `convert_units` with the value and units. "
            "Do not guess conversion factors — use the tool."
        )

    if _SIGNAL_PYTHON.search(message):
        directives.append(
            "The user wants to run Python code. "
            "You **must** call `python_exec` with the code and a 'description' of what it does. "
            "Do not simulate execution — use the tool."
        )

    if _SIGNAL_CALENDAR.search(message):
        directives.append(
            "The user is asking about their calendar. "
            "You **must** call `calendar_read` or `calendar_write` as appropriate. "
            "Ask the user which calendar ('outlook' or 'proton') if not specified."
        )

    if _SIGNAL_BRAIN_SEARCH.search(message):
        directives.append(
            "The user is asking about something from memory. "
            "You **must** call `search_thoughts` to look it up before answering."
        )

    if _SIGNAL_INVENTORY.search(message):
        directives.append(
            "The user is asking about Summit Pine inventory or production. "
            "You **must** call `sp_inventory` with the appropriate action."
        )

    if _SIGNAL_FAQ.search(message):
        directives.append(
            "The user may be asking a customer support question. "
            "Call `sp_faq` with action='search' to find the relevant answer."
        )

    if not directives:
        return ""

    lines = "\n".join(f"- {d}" for d in directives)
    return f"\n\n## Required Actions for This Request\n{lines}"


def route_model(message, requested_model):
    """Pick the right model: client override, alias, or keyword auto-route.

    Aliases: 'deep' → DEEP_MODEL, 'reasoning' → REASONING_MODEL, 'code' → CODING_MODEL.
    Auto-route checks coding keywords first (more specific), then reasoning keywords.
    """
    if requested_model == "deep":
        return DEEP_MODEL
    if requested_model == "reasoning":
        return REASONING_MODEL
    if requested_model == "code":
        return CODING_MODEL
    if requested_model is not None:
        return requested_model
    lower = message.lower()
    for kw in CODING_KEYWORDS:
        if kw in lower:
            return CODING_MODEL
    for kw in REASONING_KEYWORDS:
        if kw in lower:
            return REASONING_MODEL
    return DEFAULT_MODEL

async def handle_bootstrap_proposal(filename: str, content: str, user_id: str):
    """Create approval request, wait for resolution, write file if approved."""
    approval_id = approval_manager.create_request(
        action="bootstrap_write",
        zone="identity",
        risk_level="medium",
        description=f"Write {filename} during bootstrap",
        target=f"/agent/{filename}",
        proposed_content=content,
    )
    status = await approval_manager.wait_for_resolution(approval_id)
    if status == "approved":
        path = os.path.join(identity_module.IDENTITY_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        bootstrap.check_bootstrap_complete()


@app.post("/chat", dependencies=[Depends(_require_api_key)])
async def chat(request: ChatRequest):
    user_id = request.user_id or "default"
    trace_id = tracing.new_trace(user_id=user_id, channel=request.channel or "")

    # Build session key and load history
    session_key = f"chat:{user_id}"
    try:
        raw = redis_client.get(session_key)
        history = json.loads(raw) if raw else []
    except Exception:
        history = []

    # Append new user message
    history.append({"role": "user", "content": request.message})

    # Load identity and build system prompt
    loaded_identity = identity_module.load_identity()
    now = datetime.now(timezone.utc)
    # Prepend date so it appears before any identity content — models attend
    # more reliably to information at the start of the system prompt.
    date_line = f"Current date and time (UTC): {now.strftime('%A, %B %d, %Y %H:%M UTC')}"
    system_prompt = date_line + "\n\n" + identity_module.build_system_prompt(loaded_identity)

    memory_block = build_working_memory(user_id)
    if memory_block:
        system_prompt += "\n\n" + memory_block

    # Brain context injection (Open Brain — silent unless high-confidence or explicit)
    try:
        brain_block = await build_brain_context(request.message, channel=request.channel or "")
        if brain_block:
            system_prompt += "\n\n" + brain_block
    except Exception:
        pass

    if len(skill_registry) > 0:
        system_prompt += """

## Tool Usage
You have real-time tools available. Follow these rules strictly:

- You know the current date and time — it is given at the top of your context. \
Answer date/time questions directly and naturally. Never mention the system \
prompt, never say you lack real-time access to the date or time.
- Use **web_search** proactively for anything time-sensitive: current events, \
news, sports results, prices, weather, or any fact that may have changed since \
your training. Do not claim you lack real-time access — search instead.
- Include the current year in search queries when relevant (e.g. \
"Super Bowl 2026 winner" not "this year's Super Bowl winner").
- When search results are returned, base your answer ONLY on those results. \
Search results reflect the real world RIGHT NOW and are ALWAYS more accurate \
than your training data about current events, people, or facts. \
NEVER dismiss search results as fictional, hypothetical, or inconsistent with \
your training — your training is outdated, the search results are not. \
If search results say X is president, CEO, or any office-holder, that IS the \
correct current answer regardless of what you learned during training.
- If the first search does not answer the question fully, search again with a \
more specific query rather than guessing.
- Use **rag_search** for questions about documents the user has uploaded.
- Use **file_read** to read files from /sandbox, /agent, or /app.
- Use **file_write** to write or append files to /sandbox.
- Use **url_fetch** to retrieve content from a specific URL.
- Use **pdf_parse** to extract text from PDF files stored in /sandbox.
- Use **remember** to store important facts, preferences, or observations about \
the user that should persist across sessions.
- Use **recall** to search long-term memory for previously stored facts or \
preferences about the user.
- Use **create_task** to schedule a task or reminder for later (one-shot, \
at a specific time, or recurring).
- Use **list_tasks** to show the user's current scheduled jobs.
- Use **cancel_task** to cancel a scheduled or recurring job by its ID.
- Use **calculate** to evaluate mathematical expressions (arithmetic, trig, logs, etc.). Never compute math in your head — always use this tool.
- Use **convert_units** to convert between units (length, mass, temperature, speed, volume, etc.). Never guess conversion factors — always use this tool.
- Use **python_exec** to run Python code in a sandboxed subprocess. Always provide a 'description' of what the code does. Owner approval required before execution.
- Use **calendar_read** to check upcoming events, see what's on the calendar, or check availability. Specify calendar: "outlook" or "proton".
- Use **calendar_write** to create, update, or delete calendar events. Owner approval required. Specify calendar: "outlook" or "proton".
- Use **capture_thought** to save something to persistent brain memory (thoughts, notes, facts, decisions). Use when the user says "remember:", "/remember", or asks you to save a thought or note.
- Use **search_thoughts** to look up something from brain memory. Use when the user says "do you remember", "what did I say about", or references previous conversations.
- Use **sp_inventory** to manage Summit Pine inventory and production batches. Actions: list_all, list_low_stock, get_item, update_quantity, list_batches, get_batch, record_batch, update_batch_status. Use list_low_stock to check what needs reordering.
- Use **sp_orders** to track Summit Pine orders. Actions: list (optionally filter by status/channel), get (order_number), create, update_status. Statuses: pending, processing, shipped, delivered, refund_requested, refunded, cancelled.
- Use **sp_faq** to search or manage Summit Pine customer support FAQ. Actions: search (query), list (optional category), add (question+answer+category). Always check the guardrail field — no_medical_advice entries must refer to a dermatologist."""

        # Append a per-request forcing directive when the message contains
        # clear signals that a specific tool should be used.  This runs AFTER
        # the general guidance so it reads as an explicit override.
        forcing = _tool_forcing_directive(request.message)
        if forcing:
            system_prompt += forcing

    # Privacy directive — injected for every non-private channel.
    # This is the third layer of the privacy safeguard (after channel-gated
    # skill execution and channel-aware memory injection).
    _req_channel = request.channel or ""
    if _req_channel not in ("telegram", "cli", "mumble_owner"):
        system_prompt += f"""

## Privacy Policy — Channel Restriction
You are responding on the **{_req_channel or 'unknown'}** channel, which is not a private owner channel.

**You must NEVER share the following on this channel:**
- Owner personal details (family members' names, home address, phone, email, location)
- Calendar appointments or personal schedule
- Household facts (wifi credentials, utility accounts, home details, door codes)
- Contents of memory recalled from past conversations about personal matters
- Any information from identity files (USER.md, SOUL.md, AGENTS.md)
- Customer order details (names, emails, addresses)

If asked about personal information on this channel, say only:
"Personal details are only available on your private Telegram channel."

Business information (Summit Pine inventory, product FAQ, general knowledge) is fine to share on any channel."""

    # Voice channel: ask for short, spoken-language responses
    if request.channel in ("mumble", "mumble_owner"):
        system_prompt += (
            "\n\n## Voice Response Guidelines\n"
            "Your response will be read aloud via text-to-speech. "
            "Be concise — 1 to 4 sentences unless the question genuinely needs more. "
            "Use plain spoken prose: no markdown, no bullet points, no headers, no code fences. "
            "If you must list items, connect them naturally with words like 'and' or 'then'. "
            "Avoid starting with filler phrases like 'Certainly!' or 'Of course!'."
        )

    in_bootstrap = identity_module.is_bootstrap_mode()

    # Bootstrap is CLI-only — lock out Telegram, web-ui, and any remote caller.
    # Only 'agent bootstrap-reset' (channel="cli", local machine) may proceed.
    if in_bootstrap and request.channel != "cli":
        raise HTTPException(
            status_code=403,
            detail="Bootstrap mode is active. Use 'agent bootstrap-reset' from the local machine CLI.",
        )

    # Truncate from the front to fit within token budget (always keep latest user message)
    # During bootstrap, skip truncation — let Ollama use the full num_ctx window
    if in_bootstrap:
        truncated = list(history)
    else:
        truncated = list(history)
        dropped = []
        while len(truncated) > 1 and sum(estimate_tokens(m["content"]) for m in truncated) > HISTORY_TOKEN_BUDGET:
            dropped.append(truncated.pop(0))
        if dropped:
            asyncio.create_task(_summarise_and_store(dropped, user_id))

    # Prepend system message to the messages sent to Ollama
    ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    # Route to the appropriate model
    model = route_model(request.message, request.model)
    # When skills are available and the client did not request a specific model,
    # override to CODING_MODEL for coding tasks or TOOL_MODEL for everything else.
    if len(skill_registry) > 0 and request.model is None:
        lower = request.message.lower()
        if any(kw in lower for kw in CODING_KEYWORDS):
            model = CODING_MODEL
        else:
            model = TOOL_MODEL

    tracing.log_chat_request(
        request.message, model=model, bootstrap=in_bootstrap,
    )

    # Run through tool loop (handles both tool-calling and plain chat)
    ctx = DEEP_NUM_CTX if model in (DEEP_MODEL, CODING_MODEL) else NUM_CTX
    tools = skill_registry.to_ollama_tools() or None
    try:
        assistant_content, updated_messages, tool_stats = await run_tool_loop(
            ollama_client=ollama_client,
            messages=ollama_messages,
            tools=tools,
            model=model,
            ctx=ctx,
            skill_registry=skill_registry,
            policy_engine=policy_engine,
            approval_manager=approval_manager,
            auto_approve=request.auto_approve,
            user_id=user_id,
            max_iterations=MAX_TOOL_ITERATIONS,
            channel=request.channel or "",
        )
    except Exception as e:
        err_msg = str(e)
        tracing._emit("chat", {"status": "error", "model": model, "error": err_msg})
        raise HTTPException(status_code=503, detail=f"Model error ({model}): {err_msg}")

    # Ollama per-request metrics are not available from a multi-turn tool loop;
    # per-skill timing is captured via log_skill_call inside execute_skill.
    tracing.log_chat_response(
        model=model,
        response_preview=assistant_content,
        eval_count=0,
        prompt_eval_count=0,
        total_duration_ms=0,
        tool_iterations=tool_stats["iterations"],
        skills_called=tool_stats["skills_called"],
    )

    # In bootstrap mode, check for file proposals
    if in_bootstrap:
        proposals = bootstrap.extract_proposals(assistant_content)
        if proposals:
            display_response = bootstrap.strip_proposals(assistant_content)
            for filename, content in proposals:
                ok, reason = bootstrap.validate_proposal(filename, content)
                if ok:
                    if request.auto_approve:
                        path = os.path.join(identity_module.IDENTITY_DIR, filename)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(content)
                        bootstrap.check_bootstrap_complete()
                    else:
                        asyncio.create_task(
                            handle_bootstrap_proposal(filename, content, user_id)
                        )
            assistant_content = display_response

    # Append assistant response and save full history
    history.append({"role": "assistant", "content": assistant_content})
    redis_client.set(session_key, json.dumps(history))

    return {"response": assistant_content, "model": model, "trace_id": trace_id}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/calendar-auth")
async def calendar_auth():
    """Start MS Graph device code flow. Visit the URL shown and enter the code.
    The token is saved automatically once you complete auth in the browser.
    Accessible on localhost only (no API key required — owner-only endpoint).
    """
    from calendar_auth import init_device_flow, complete_device_flow

    async def _complete(flow):
        try:
            await asyncio.to_thread(complete_device_flow, flow)
        except Exception:
            pass

    flow = init_device_flow()
    asyncio.create_task(_complete(flow))
    return {"message": flow["message"], "expires_in": flow.get("expires_in")}

@app.get("/bootstrap/status")
async def bootstrap_status():
    return {"bootstrap": identity_module.is_bootstrap_mode()}

@app.get("/chat/history/{user_id}", dependencies=[Depends(_require_api_key)])
async def chat_history(user_id: str):
    """Retrieve conversation history for a session."""
    session_key = f"chat:{user_id}"
    raw = redis_client.get(session_key)
    history = json.loads(raw) if raw else []
    return {"history": history}

@app.post("/policy/reload", dependencies=[Depends(_require_api_key)])
async def policy_reload():
    """Hot-reload policy.yaml without restarting the container."""
    app.state.policy_engine.load_config()
    return {"status": "reloaded"}

@app.on_event("startup")
async def startup():
    # Wire runtime state for heartbeat job runner
    app.state.ollama_client = ollama_client
    app.state.skill_registry = skill_registry
    app.state.tool_model = TOOL_MODEL
    app.state.num_ctx = NUM_CTX
    app.state.max_tool_iterations = MAX_TOOL_ITERATIONS
    app.state.job_manager = JobManager(redis_client)
    start_heartbeat(app.state)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
