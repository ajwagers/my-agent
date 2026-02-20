from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from ollama import Client
import asyncio
import os
import json
import redis
from datetime import datetime, timezone

from policy import PolicyEngine
from approval import ApprovalManager
from approval_endpoints import router as approval_router
import identity as identity_module
import bootstrap
import tracing
from skills.registry import SkillRegistry
from skills.rag_search import RagSearchSkill
from skills.web_search import WebSearchSkill
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

# Approval REST endpoints
app.include_router(approval_router)

# Skill registry
skill_registry = SkillRegistry()
skill_registry.register(RagSearchSkill())
skill_registry.register(WebSearchSkill())

# Config
HISTORY_TOKEN_BUDGET = int(os.getenv("HISTORY_TOKEN_BUDGET", "6000"))
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "phi3:latest")
REASONING_MODEL = os.getenv("REASONING_MODEL", "llama3.1:8b")
DEEP_MODEL = os.getenv("DEEP_MODEL", "qwen2.5:14b")
DEEP_NUM_CTX = int(os.getenv("DEEP_NUM_CTX", "16384"))
REASONING_KEYWORDS = [
    "explain", "analyze", "plan", "code", "why", "compare",
    "debug", "reason", "think", "step by step", "how does", "what if",
]
TOOL_MODEL = os.getenv("TOOL_MODEL", "llama3.1:8b")
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "5"))

def estimate_tokens(text):
    """Rough chars-to-tokens heuristic."""
    return len(text) // 4

class ChatRequest(BaseModel):
    message: str
    model: str = None  # None = auto-route
    user_id: str = None
    channel: str = None
    auto_approve: bool = False
    history: list = None  # Optional: client-provided conversation history

def route_model(message, requested_model):
    """Pick the right model: client override, 'deep'/'reasoning' alias, or auto-route."""
    if requested_model == "deep":
        return DEEP_MODEL
    if requested_model == "reasoning":
        return REASONING_MODEL
    if requested_model is not None:
        return requested_model
    lower = message.lower()
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
- Use **rag_search** for questions about documents the user has uploaded."""
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
        while len(truncated) > 1 and sum(estimate_tokens(m["content"]) for m in truncated) > HISTORY_TOKEN_BUDGET:
            truncated.pop(0)

    # Prepend system message to the messages sent to Ollama
    ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    # Route to the appropriate model
    model = route_model(request.message, request.model)
    # When skills are available and the client did not request a specific model,
    # override to TOOL_MODEL which is fine-tuned for tool/function calling.
    if len(skill_registry) > 0 and request.model is None:
        model = TOOL_MODEL

    tracing.log_chat_request(
        request.message, model=model, bootstrap=in_bootstrap,
    )

    # Run through tool loop (handles both tool-calling and plain chat)
    ctx = DEEP_NUM_CTX if model == DEEP_MODEL else NUM_CTX
    tools = skill_registry.to_ollama_tools() or None
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
