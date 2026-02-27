from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from ollama import Client
import asyncio
import os
import json
import time
import redis
from datetime import datetime, timezone

from policy import PolicyEngine
from approval import ApprovalManager
from approval_endpoints import router as approval_router
import identity as identity_module
import bootstrap
import tracing
from memory import MemoryStore
from heartbeat import start_heartbeat
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
from skill_runner import run_tool_loop

app = FastAPI()
ollama_client = Client(host='http://ollama-runner:11434')

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

# Long-term memory singleton — used for working memory injection in system prompt
memory_store = MemoryStore()

# Config
HISTORY_TOKEN_BUDGET = int(os.getenv("HISTORY_TOKEN_BUDGET", "6000"))
NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "phi4-mini:latest")
REASONING_MODEL = os.getenv("REASONING_MODEL", "qwen3:8b")
DEEP_MODEL = os.getenv("DEEP_MODEL", "qwen2.5:14b")
DEEP_NUM_CTX = int(os.getenv("DEEP_NUM_CTX", "32768"))
CODING_MODEL = os.getenv("CODING_MODEL", "codegemma:latest")
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
        response = ollama_client.chat(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            options={"num_ctx": 2048},
        )
        summary = response["message"].get("content", "").strip()
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
Do not mix in facts from your training data. Do not invent details not present \
in the results.
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
preferences about the user."""
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
    start_heartbeat(app.state)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
