from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
from ollama import AsyncClient
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
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
import metrics
from memory import MemoryStore
from heartbeat import start_heartbeat, seed_default_jobs
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
from skills.sp_costs import SummitPineCostsSkill
from skills.sp_time_log import SummitPineTimeLogSkill
from skills.sp_recipes import SummitPineRecipesSkill
from skills.sp_promotions import SummitPinePromotionsSkill
from skills.todo import TodoSkill
from skills.shell_exec import ShellExecSkill
from skills.github_skill import GitHubSkill
from skill_runner import run_tool_loop, gather_tool_context, execute_skill
from memory_middleware import build_brain_context
from personas import PersonaRegistry

app = FastAPI()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
REASONING_MODEL = os.getenv("REASONING_MODEL", "gemma4:e4b")
ollama_client = AsyncClient(host=OLLAMA_HOST, timeout=None)

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
skill_registry.register(SummitPineCostsSkill())
skill_registry.register(SummitPineTimeLogSkill())
skill_registry.register(SummitPineRecipesSkill())
skill_registry.register(SummitPinePromotionsSkill())
skill_registry.register(TodoSkill())
skill_registry.register(ShellExecSkill(ollama_host=OLLAMA_HOST, reasoning_model=REASONING_MODEL))
skill_registry.register(GitHubSkill())

# Persona registry — loaded from personas.yaml seed, backed by Redis
import os as _os
_PERSONAS_YAML = _os.path.join(_os.path.dirname(__file__), "personas.yaml")
persona_registry = PersonaRegistry(redis_client, yaml_path=_PERSONAS_YAML)

# Register persona-management skills
from skills.create_persona import CreatePersonaSkill
from skills.list_personas import ListPersonasSkill
from skills.delete_persona import DeletePersonaSkill
from skills.switch_persona import SwitchPersonaSkill

skill_registry.register(CreatePersonaSkill(persona_registry))
skill_registry.register(ListPersonasSkill(persona_registry))
skill_registry.register(DeletePersonaSkill(persona_registry))
skill_registry.register(SwitchPersonaSkill(persona_registry))

# Long-term memory singleton — used for working memory injection in system prompt
memory_store = MemoryStore()

# Config
HISTORY_TOKEN_BUDGET = int(os.getenv("HISTORY_TOKEN_BUDGET", "6000"))
NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "phi4-mini:latest")
DEEP_MODEL = os.getenv("DEEP_MODEL", "qwen2.5:14b")
DEEP_NUM_CTX = int(os.getenv("DEEP_NUM_CTX", "32768"))
CODING_MODEL = os.getenv("CODING_MODEL", "gemma4:e4b")
REASONING_KEYWORDS = [
    "explain", "analyze", "plan", "why", "compare",
    "reason", "think", "step by step", "how does", "what if",
]
CODING_KEYWORDS = [
    "code", "debug", "implement", "refactor", "function", "class",
    "script", "bug", "fix", "test", "write a program", "write a script",
    "write a function", "write a class", "write a test", "unit test",
]
TOOL_MODEL = os.getenv("TOOL_MODEL", "gemma4:e4b")
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "10"))

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
    message: str = ""
    model: str = None  # None = auto-route
    user_id: str = None
    channel: str = None
    auto_approve: bool = False
    history: list = None  # Optional: client-provided conversation history
    persona: str = None   # Optional: persona slug; if None, looks up session from Redis
    image_base64: str = None  # Optional: base64-encoded image for OCR (receipt scanning)


def _ocr_image_sync(image_base64: str) -> str:
    """Synchronous OCR on a base64-encoded image. Runs in a thread pool."""
    import base64 as _b64
    import io
    from PIL import Image, ImageEnhance
    import pytesseract

    img_bytes = _b64.b64decode(image_base64)
    img = Image.open(io.BytesIO(img_bytes)).convert("L")  # Grayscale
    # Boost contrast so faint receipt text reads cleanly
    img = ImageEnhance.Contrast(img).enhance(2.0)
    # Upscale small images (phone crops) so tesseract has enough resolution
    w, h = img.size
    if w < 1000:
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
    text = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
    return text.strip()


async def _ocr_image(image_base64: str) -> str:
    try:
        return await asyncio.to_thread(_ocr_image_sync, image_base64)
    except Exception as exc:
        return f"[OCR failed: {exc}]"

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

_SIGNAL_HOURS = re.compile(
    r"\b(?:i\s+)?worked\b.{0,40}(?:hour|hr)|"
    r"\b(?:i\s+)?(?:spent|put in)\b.{0,20}(?:hour|hr)|"
    r"\bstarted\s+at\s+\d|"
    r"\bfinished\s+at\s+\d|\bended\s+at\s+\d|"
    r"\blog\s+(?:my\s+)?(?:hours?|time|labour|labor)\b|"
    r"\btime\s+log\b|\bhours?\s+(?:today|yesterday|this week)\b",
    re.IGNORECASE,
)

_SIGNAL_TODO = re.compile(
    r"\bi need to\b|\bi need to (?:buy|get|pick up|order)\b|"
    r"\badd (?:to|this) (?:my )?(?:list|todo|to-do|shopping list|task list)\b|"
    r"\bremind me to\b|\bdon'?t forget(?: to)?\b|"
    r"\bput (?:it |this )?on (?:my )?list\b|"
    r"\bmark (?:it |that )?(?:as )?done\b|\bdone with\b|\bfinished\b.{0,20}\btask\b|"
    r"\bcomplete (?:todo|task|item) #?\d|\bmark #?\d+ done\b|"
    r"\bwhat'?s on my (?:list|todo|to-do|shopping list)\b|"
    r"\bshow (?:me )?my (?:list|todos|tasks|shopping list)\b",
    re.IGNORECASE,
)

# Extracts the task/item text from "I need to X" / "remind me to X" patterns.
# Used by the pre-processor to call the todo skill directly before the LLM.
_TODO_ADD_EXTRACT = re.compile(
    r"^(?:i need to |remind me to |don'?t forget(?: to)? |"
    r"add (?:this )?to (?:my )?(?:list|todo|to-do)[:\s]*)(.+?)\.?$",
    re.IGNORECASE | re.DOTALL,
)
_TODO_PURCHASE_WORDS = frozenset({
    "buy", "get", "pick up", "order", "purchase", "groceries",
    "supplies", "parts", "materials", "stripping", "film",
})

_SIGNAL_PROMOTIONS = re.compile(
    r"\bpromotion\b|\bpromo\b|\bdiscount\s+code\b|"
    r"\bsale\b.{0,30}\b(?:start|end|run|create|set up)\b|"
    r"\bcoupon\b|\boffer\b.{0,20}\b(?:expire|end|start)\b",
    re.IGNORECASE,
)

# Complex output: code gen, research, analysis, or detailed planning.
# Triggers a self-critique reflection pass on the final response.
_SIGNAL_REFLECTION = re.compile(
    r"```\w*\n|"                                              # fenced code block in message
    r"\bwrite\s+(?:a\s+)?(?:function|class|script|program|module|test|unittest)\b|"
    r"\bimplement\b|\brefactor\b|\bdebug\b|"
    r"\bresearch\b|\binvestigate\b|"
    r"\banalyze\b|\banalysis\b|\banalyse\b|"
    r"\bplan\s+(?:for|to|out|a|the)\b|\bstrategy\s+(?:for|to)\b|\bproposal\b|"
    r"\bin[\s-]depth\b|\bcomprehensive\b|"
    r"\bpros\s+and\s+cons\b|\bcompare\b.{0,40}\bwith\b",
    re.IGNORECASE,
)

# Multi-step intent: the request involves several discrete actions or requires
# research + synthesis.  Triggers two-pass plan→execute.
_SIGNAL_MULTISTEP = re.compile(
    r"\bthen\b.{0,60}\band\b.{0,60}\b(?:then|after|finally)\b|"
    r"\bafter (?:that|which|you|doing)\b|"
    r"\bfirst.{0,60}\bthen\b|"
    r"\bsteps?\b.{0,30}\bto\b|"
    r"\b(?:research|find|look up|check|get).{0,80}"
    r"(?:then|and).{0,80}(?:create|write|make|build|send|schedule|update)\b|"
    r"\b(?:plan|workflow|outline) (?:for|to|the)\b",
    re.IGNORECASE,
)

# Matches receipt/invoice documents AND plain-English purchase descriptions.
_SIGNAL_EXPENSE = re.compile(
    # Explicit document types
    r"\breceipts?\b|\binvoices?\b|\bexpenses?\b|"
    # Explicit log/record commands
    r"log (?:this|these|an?|the)\b.{0,50}(?:expense|receipt|purchase|order)|"
    r"record (?:this|an?|the)\b.{0,50}(?:expense|purchase)|"
    r"ingest.{0,50}(?:receipt|invoice|expense|data)|"
    # Receipt document markers (OCR output)
    r"\bTOTAL\s*:|\bSUBTOTAL\b|\bAMOUNT\s+DUE\b|"
    r"\[File:[^\]]*(?:receipt|invoice|expense|order|purchase)[^\]]*\]|"
    # Plain-English purchase descriptions
    r"\b(?:i\s+)?(?:bought|purchased|ordered|picked\s+up)\b.{0,80}\$|"
    r"\b(?:i\s+)?(?:bought|purchased|ordered|picked\s+up)\b.{0,40}(?:from|at)\b|"
    r"\$\s*\d[\d,.]*\s*(?:for|from|at|worth)|"
    r"\bpaid\s+\$\s*\d|"
    r"\bspent\s+\$\s*\d",
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

    # Inventory docs and receipts can overlap — prefer inventory directive when the
    # message explicitly mentions "inventory" or contains multiple raw-material SKUs.
    _is_inventory_doc = bool(
        re.search(r"\binventory\b|\bstock count\b|\bquantities\b|\bquantity on hand\b", message, re.IGNORECASE)
        or len(re.findall(r"\b(?:RAW|PKG|SP)-[A-Z]+\b", message)) >= 3
    )

    if _SIGNAL_EXPENSE.search(message) and not _is_inventory_doc:
        directives.append(
            "The user is sharing purchase or expense data — either a scanned receipt or a "
            "plain-English description of items they bought. "
            "You **must** call `sp_costs` with action=log_expense for each distinct vendor or "
            "transaction. Extract from the text: description (what was bought), amount (USD), "
            "category (ingredients/packaging/equipment/shipping/marketing/other), supplier "
            "(store or vendor name), and date (today if not stated). "
            "Do NOT summarise or ask for clarification — log every purchase you can identify, "
            "then confirm with a brief list of what was recorded."
        )

    if _SIGNAL_INVENTORY.search(message):
        if _is_inventory_doc:
            directives.append(
                "The user is sharing an inventory count or stock list. "
                "You **must** call `sp_inventory` with action=bulk_update, passing all SKU quantities "
                "as the updates array: [{\"sku\": \"RAW-COCO\", \"quantity\": 5000}, ...]. "
                "Include every item from the list in one call."
            )
        else:
            directives.append(
                "The user is asking about Summit Pine inventory or production. "
                "You **must** call `sp_inventory` with the appropriate action."
            )

    if _SIGNAL_FAQ.search(message):
        directives.append(
            "The user may be asking a customer support question. "
            "Call `sp_faq` with action='search' to find the relevant answer."
        )

    if _SIGNAL_HOURS.search(message):
        directives.append(
            "The user is reporting hours worked or a work session. "
            "You **must** call `sp_time_log` with action=log_hours. "
            "Parse the hours: compute from start/end times if given, or use the stated number directly. "
            "Default log_date to today. Default person to 'owner'. "
            "Extract task_description if mentioned. Confirm what was logged."
        )

    if _SIGNAL_TODO.search(message):
        directives.append(
            "The user wants to manage their to-do or shopping list. "
            "You MUST call `todo`. "
            "For adding: infer category — 'purchase' if they want to buy/get something, "
            "'errand' if they need to go somewhere, 'task' for everything else. "
            "For listing: use action=list. "
            "For completing: use action=complete with the id. "
            "Confirm briefly what was added or changed."
        )

    if _SIGNAL_PROMOTIONS.search(message):
        directives.append(
            "The user is asking about promotions or discounts. "
            "Call `sp_promotions` with the appropriate action (create, list, get, update, or deactivate)."
        )

    if not directives:
        return ""

    lines = "\n".join(f"- {d}" for d in directives)
    return f"\n\n## Required Actions for This Request\n{lines}"


# ── Focused registry ────────────────────────────────────────────────────────
# Skills always available regardless of message content.
_ALWAYS_ON_SKILLS: frozenset = frozenset({
    "web_search", "url_fetch",
    "calculate", "convert_units",
    "remember", "recall", "capture_thought", "search_thoughts",
    "todo", "create_task", "list_tasks", "cancel_task",
    "list_personas", "switch_persona", "create_persona", "delete_persona",
})

# Signal → additional skills to unlock when the signal fires.
_SIGNAL_SKILL_GATES = [
    (_SIGNAL_FILE,       frozenset({"file_read", "file_write", "pdf_parse"})),
    (_SIGNAL_PYTHON,     frozenset({"python_exec"})),
    (_SIGNAL_CALENDAR,   frozenset({"calendar_read", "calendar_write"})),
    (_SIGNAL_INVENTORY,  frozenset({"sp_inventory", "sp_orders", "sp_faq",
                                    "sp_costs", "sp_time_log", "sp_recipes",
                                    "sp_promotions"})),
    (_SIGNAL_EXPENSE,    frozenset({"sp_costs"})),
    (_SIGNAL_HOURS,      frozenset({"sp_time_log"})),
    (_SIGNAL_FAQ,        frozenset({"sp_faq"})),
    (_SIGNAL_PROMOTIONS, frozenset({"sp_promotions"})),
]


def _build_focused_registry(message: str, effective_registry):
    """Return a narrowed SkillRegistry containing only skills relevant to this
    message.  Falls back to effective_registry unchanged if the result would be
    trivially small (< 4 skills), so we never accidentally strip too much.

    Only called when the active persona has no explicit skill restriction
    (allowed_skills is None), i.e. the default open-access persona.
    """
    from skills.registry import SkillRegistry as _SR
    enabled = set(_ALWAYS_ON_SKILLS)
    for pattern, extra_skills in _SIGNAL_SKILL_GATES:
        if pattern.search(message):
            enabled.update(extra_skills)

    focused = _SR()
    for name in enabled:
        skill = effective_registry.get(name)
        if skill:
            try:
                focused.register(skill)
            except ValueError:
                pass

    if len(focused) < 4:
        return effective_registry
    return focused


# ── Two-pass planning ────────────────────────────────────────────────────────
_PLAN_SYSTEM = (
    "You are a precise planning assistant. "
    "The user has a complex multi-step request. "
    "Produce a numbered execution plan of 3-6 concrete steps. "
    "Reference specific tool names where a tool call is needed. "
    "Be brief — one sentence per step. "
    "Output only the numbered list, no prose before or after."
)


async def _generate_plan(message: str, tool_names: list, ctx: int) -> str | None:
    """Run a single think=True call to produce an execution plan.

    Returns the plan text, or None if planning fails or produces no useful output.
    """
    tools_line = ", ".join(tool_names[:20]) if tool_names else "none"
    user_content = f"Available tools: {tools_line}\n\nRequest: {message}"
    try:
        response = await ollama_client.chat(
            model=REASONING_MODEL,
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            options={"num_ctx": min(ctx, 4096)},
            think=True,
        )
        plan = (response.message.content or "").strip()
        if not plan or len(plan.strip().splitlines()) < 2:
            return None
        return plan
    except Exception:
        return None


# ── Reflection / self-critique ───────────────────────────────────────────────
_REFLECT_SYSTEM = (
    "You are a quality reviewer for an AI assistant. "
    "You will receive a user request and a draft response. "
    "Check the draft for: completeness, correctness, missing steps, "
    "logic errors, and whether it fully addresses the request. "
    "If the draft is already complete and correct, output it verbatim — no changes. "
    "If it has errors, gaps, or unclear explanations, output a corrected version. "
    "Output ONLY the final response. "
    "No preamble, no 'here is the revised version', no commentary."
)
_REFLECT_MIN_CHARS = 300  # don't reflect on short answers


async def _reflect(user_message: str, draft: str, ctx: int) -> str:
    """Self-critique pass: returns a (possibly revised) version of draft.

    Uses REASONING_MODEL with think=True so the model reasons through the
    critique before writing the output.  Falls back to draft on any error.
    """
    if len(draft) < _REFLECT_MIN_CHARS:
        return draft
    user_content = f"User request:\n{user_message}\n\nDraft response:\n{draft}"
    try:
        response = await ollama_client.chat(
            model=REASONING_MODEL,
            messages=[
                {"role": "system", "content": _REFLECT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            options={"num_ctx": min(ctx, 8192)},
            think=True,
        )
        result = (response.message.content or "").strip()
        return result if result else draft
    except Exception:
        return draft


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

    # Resolve active persona: explicit request field > session store > default
    active_persona_name = request.persona or persona_registry.get_session(user_id) or "default"
    active_persona = persona_registry.get(active_persona_name) or persona_registry.get("default")

    # Build session key — non-default personas get separate history namespaces
    session_key = (
        f"chat:{user_id}:{active_persona_name}"
        if active_persona_name != "default"
        else f"chat:{user_id}"
    )
    try:
        raw = redis_client.get(session_key)
        history = json.loads(raw) if raw else []
    except Exception:
        history = []

    # OCR preprocessing for attached images (receipt scanning)
    if request.image_base64:
        ocr_text = await _ocr_image(request.image_base64)
        scan_block = f"[Scanned document]\n{ocr_text}\n[End scan]"
        user_message = (
            f"{scan_block}\n\n{request.message}"
            if request.message.strip()
            else f"{scan_block}\n\nPlease extract and log the expenses from this receipt."
        )
    else:
        if not request.message.strip():
            raise HTTPException(status_code=400, detail="message or image_base64 is required")
        user_message = request.message

    # Append new user message
    history.append({"role": "user", "content": user_message})

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

    # Persona system prompt overlay
    if active_persona and active_persona.system_prompt_extra:
        system_prompt += f"\n\n{active_persona.system_prompt_extra}"

    # Tool usage block is built after effective_registry so instructions only
    # mention skills that are actually available for this persona.
    _SKILL_INSTRUCTIONS: dict = {
        "web_search": (
            "- Use **web_search** proactively for anything time-sensitive: current events, "
            "news, sports results, prices, weather, or any fact that may have changed since "
            "your training. Do not claim you lack real-time access — search instead.\n"
            "- Include the current year in search queries when relevant (e.g. "
            "\"Super Bowl 2026 winner\" not \"this year's Super Bowl winner\").\n"
            "- When search results are returned, base your answer ONLY on those results. "
            "Search results reflect the real world RIGHT NOW and are ALWAYS more accurate "
            "than your training data about current events, people, or facts. "
            "NEVER dismiss search results as fictional, hypothetical, or inconsistent with "
            "your training — your training is outdated, the search results are not. "
            "If search results say X is president, CEO, or any office-holder, that IS the "
            "correct current answer regardless of what you learned during training.\n"
            "- If the first search does not answer the question fully, search again with a "
            "more specific query rather than guessing."
        ),
        "rag_search": "- Use **rag_search** for questions about documents the user has uploaded.",
        "file_read": "- Use **file_read** to read files from /sandbox, /agent, or /app.",
        "file_write": "- Use **file_write** to write or append files to /sandbox.",
        "url_fetch": "- Use **url_fetch** to retrieve content from a specific URL.",
        "pdf_parse": "- Use **pdf_parse** to extract text from PDF files stored in /sandbox.",
        "remember": "- Use **remember** to store important facts, preferences, or observations about the user that should persist across sessions.",
        "recall": "- Use **recall** to search long-term memory for previously stored facts or preferences about the user.",
        "create_task": "- Use **create_task** to schedule a task or reminder for later (one-shot, at a specific time, or recurring).",
        "list_tasks": "- Use **list_tasks** to show the user's current scheduled jobs.",
        "cancel_task": "- Use **cancel_task** to cancel a scheduled or recurring job by its ID.",
        "calculate": "- Use **calculate** to evaluate mathematical expressions (arithmetic, trig, logs, etc.). Never compute math in your head — always use this tool.",
        "convert_units": "- Use **convert_units** to convert between units (length, mass, temperature, speed, volume, etc.). Never guess conversion factors — always use this tool.",
        "python_exec": "- Use **python_exec** to run Python code in a sandboxed subprocess. Always provide a 'description' of what the code does. Owner approval required before execution.",
        "calendar_read": "- Use **calendar_read** to check upcoming events, see what's on the calendar, or check availability. Specify calendar: \"outlook\" or \"proton\".",
        "calendar_write": "- Use **calendar_write** to create, update, or delete calendar events. Owner approval required. Specify calendar: \"outlook\" or \"proton\".",
        "capture_thought": "- Use **capture_thought** to save something to persistent brain memory (thoughts, notes, facts, decisions). Use when the user says \"remember:\", \"/remember\", or asks you to save a thought or note.",
        "search_thoughts": "- Use **search_thoughts** to look up something from brain memory. Use when the user says \"do you remember\", \"what did I say about\", or references previous conversations.",
        "sp_inventory": "- Use **sp_inventory** to manage Summit Pine inventory and production batches. Actions: list_all, list_low_stock, get_item, update_quantity, bulk_update (pass updates=[{sku,quantity},...] to update many quantities at once — preferred for full inventory counts), list_batches, get_batch, record_batch, update_batch_status. Prefer bulk_update when loading or refreshing a full inventory list.",
        "sp_orders": "- Use **sp_orders** to track Summit Pine orders. Actions: list (optionally filter by status/channel), get (order_number), create, update_status. Statuses: pending, processing, shipped, delivered, refund_requested, refunded, cancelled.",
        "sp_faq": "- Use **sp_faq** to search or manage Summit Pine customer support FAQ. Actions: search (query), list (optional category), add (question+answer+category). Always check the guardrail field — no_medical_advice entries must refer to a dermatologist.",
        "sp_costs": "- Use **sp_costs** to track Summit Pine expenses and compute COGS/P&L. Actions: log_expense (record a purchase), list_expenses (filter by date/category), expense_summary (totals by category for a month), batch_cogs (ingredient cost for a batch), profit_summary (revenue - expenses for a month).",
        "sp_time_log": "- Use **sp_time_log** to track labour hours. Actions: log_hours (record a work session — parse start/end times or stated hours), list_hours (view time log), time_summary (totals by person for a month). Call this whenever the user mentions working hours, production time, or labour.",
        "sp_recipes": "- Use **sp_recipes** to manage Summit Pine production recipes. Actions: add (create), get (by ID), list (all or filtered by tag), update, delete. Ingredients format: [{\"name\": \"coconut oil\", \"amount\": \"200\", \"unit\": \"g\"}].",
        "sp_promotions": "- Use **sp_promotions** to manage promotions and discount codes. Actions: create, list (active_only=true by default), get, update, deactivate. Discount types: percent, fixed_amount, free_shipping, buy_x_get_y.",
        "list_personas": "- Use **list_personas** to show available agent personas.",
        "switch_persona": "- Use **switch_persona** to switch to a different agent persona (e.g. 'summit_pine', 'default').",
        "create_persona": "- Use **create_persona** to create a new agent persona with a custom name, personality, and optional skill restriction.",
        "delete_persona": "- Use **delete_persona** to remove a user-created persona by name.",
        "todo": (
            "- Use **todo** to manage a personal to-do and shopping list. "
            "action=add to add a task/item (category: task|purchase|errand, priority: low|medium|high). "
            "action=list to show pending items. "
            "action=complete with id to mark done. "
            "action=delete with id to remove. "
            "Use this whenever the user says they need to do/buy/get something, or asks to see their list."
        ),
    }

    # Privacy directive — injected for every non-private channel.
    # This is the third layer of the privacy safeguard (after channel-gated
    # skill execution and channel-aware memory injection).
    _req_channel = request.channel or ""
    if _req_channel not in ("telegram", "cli", "mumble_owner", "web-ui"):
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
    model = route_model(user_message, request.model)
    # When skills are available and the client did not request a specific model,
    # override to CODING_MODEL for coding tasks or TOOL_MODEL for everything else.
    if len(skill_registry) > 0 and request.model is None:
        lower = user_message.lower()
        if any(kw in lower for kw in CODING_KEYWORDS):
            model = CODING_MODEL
        else:
            model = TOOL_MODEL

    tracing.log_chat_request(
        user_message, model=model, bootstrap=in_bootstrap,
    )

    # Run through tool loop (handles both tool-calling and plain chat)
    ctx = DEEP_NUM_CTX if model in (DEEP_MODEL, CODING_MODEL) else NUM_CTX
    # Apply persona skill filter — build an effective registry for this request
    if active_persona and active_persona.allowed_skills is not None:
        from skills.registry import SkillRegistry as _SR
        effective_registry = _SR()
        for _sname in active_persona.allowed_skills:
            _s = skill_registry.get(_sname)
            if _s:
                effective_registry.register(_s)
        # Always include persona management skills regardless of persona filter
        for _mgmt in ("list_personas", "switch_persona", "create_persona", "delete_persona"):
            _s = skill_registry.get(_mgmt)
            if _s and not effective_registry.get(_mgmt):
                effective_registry.register(_s)
    else:
        effective_registry = skill_registry

    # Narrow tool list to only skills relevant to this message (default persona only)
    if not (active_persona and active_persona.allowed_skills is not None):
        effective_registry = _build_focused_registry(user_message, effective_registry)

    tools = effective_registry.to_ollama_tools() or None

    # Build tool usage block using only skills in the effective registry,
    # so the model is never told to use a tool that isn't available.
    if tools:
        _available = {s for s in effective_registry._skills}
        _skill_lines = "\n".join(
            v for k, v in _SKILL_INSTRUCTIONS.items() if k in _available
        )
        _tool_block = (
            "\n\n## Tool Usage\n"
            "You have real-time tools available. Follow these rules strictly:\n\n"
            "- You know the current date and time — it is given at the top of your context. "
            "Answer date/time questions directly and naturally. Never mention the system "
            "prompt, never say you lack real-time access to the date or time.\n"
        )
        if _skill_lines:
            _tool_block += _skill_lines
        system_prompt += _tool_block
        # Forcing directive (must come after general guidance)
        forcing = _tool_forcing_directive(user_message)
        if forcing:
            system_prompt += forcing

        # Receipt image: force sp_costs when present and available (but not for inventory images)
        _img_is_inventory = bool(
            re.search(r"\binventory\b|\bstock count\b|\bquantities\b|\bquantity on hand\b", user_message, re.IGNORECASE)
            or len(re.findall(r"\b(?:RAW|PKG|SP)-[A-Z]+\b", user_message)) >= 3
        )
        if request.image_base64 and "sp_costs" in _available and not _img_is_inventory:
            system_prompt += (
                "\n\n## Receipt Processing\n"
                "A scanned document has been injected into the user message above. "
                "If it contains purchase or expense data (receipt, invoice, or order confirmation), "
                "you MUST call `sp_costs` with action=log_expense to record it. "
                "Extract: vendor (→supplier field), date, total amount, and infer the category "
                "(ingredients/packaging/equipment/shipping/marketing/other based on vendor and items). "
                "After logging, confirm with a brief summary of what was recorded."
            )

    # Rebuild ollama_messages — system_prompt was modified above (tool block appended)
    ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    # Two-pass planning: for multi-step requests, generate an execution plan first
    # so the model enters the tool loop with a clear roadmap.
    if _SIGNAL_MULTISTEP.search(user_message) and tools:
        _tool_names = [t["function"]["name"] for t in tools]
        _plan = await _generate_plan(user_message, _tool_names, ctx)
        if _plan:
            system_prompt += f"\n\n## Execution Plan\n{_plan}"
            ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    # ── Pre-process: auto-add todo items without relying on the model ──────────
    # qwen3:8b consistently ignores todo tool directives for "I need to X"
    # messages, responding conversationally instead. For unambiguous add-intent
    # phrases we call the skill directly and tell the model what happened, so
    # it can just confirm. Skip if a more specific SP/expense/calendar signal
    # also fired — those skills should handle it instead.
    _todo_pre_result = None
    _todo_skill_obj = effective_registry.get("todo") if "todo" in getattr(effective_registry, "_skills", {}) else None
    if _todo_skill_obj and _SIGNAL_TODO.search(user_message):
        _sp_signal = (
            _SIGNAL_INVENTORY.search(user_message)
            or _SIGNAL_EXPENSE.search(user_message)
            or _SIGNAL_CALENDAR.search(user_message)
            or _SIGNAL_HOURS.search(user_message)
        )
        # Only auto-add for plain "I need to / remind me to" patterns,
        # not for list/complete/delete operations (those still go through the model).
        _add_match = _TODO_ADD_EXTRACT.match(user_message.strip())
        if _add_match and not _sp_signal:
            _task_text = _add_match.group(1).strip().rstrip(".")
            _lower_msg = user_message.lower()
            _cat = "purchase" if any(w in _lower_msg for w in _TODO_PURCHASE_WORDS) else "task"
            _todo_pre_result = await execute_skill(
                skill=_todo_skill_obj,
                params={"action": "add", "text": _task_text, "category": _cat},
                policy_engine=policy_engine,
                approval_manager=approval_manager,
                auto_approve=True,
                user_id=user_id,
                channel=request.channel or "",
                persona=active_persona_name,
            )
            if _todo_pre_result and "error" not in _todo_pre_result.lower():
                # Item saved — return a simple confirmation directly.
                # Do NOT run the model: qwen3:8b ignores the system note and
                # either double-saves or offers to save something already saved.
                try:
                    _saved_data = json.loads(_todo_pre_result)
                    _saved_label = _saved_data.get("added", _task_text)
                except Exception:
                    _saved_label = _task_text
                _cat_word = "shopping list" if _cat == "purchase" else "to-do list"
                _confirm = f"Got it — added \"{_task_text}\" to your {_cat_word}."
                history.append({"role": "assistant", "content": _confirm})
                redis_client.set(session_key, json.dumps(history))
                tracing.log_chat_response(
                    model=model,
                    response_preview=_confirm,
                    eval_count=0,
                    prompt_eval_count=0,
                    total_duration_ms=0,
                    tool_iterations=0,
                    skills_called=["todo"],
                )
                return {"response": _confirm, "model": model, "trace_id": trace_id}
            else:
                _todo_pre_result = None  # failed — let the model try normally

    try:
        assistant_content, updated_messages, tool_stats = await run_tool_loop(
            ollama_client=ollama_client,
            messages=ollama_messages,
            tools=tools,
            model=model,
            ctx=ctx,
            skill_registry=effective_registry,
            policy_engine=policy_engine,
            approval_manager=approval_manager,
            auto_approve=request.auto_approve,
            user_id=user_id,
            max_iterations=MAX_TOOL_ITERATIONS,
            channel=request.channel or "",
            persona=active_persona_name,
        )
    except Exception as e:
        err_msg = str(e) or f"({type(e).__name__} with no message)"
        tracing._emit("chat", {"status": "error", "model": model, "error": err_msg})
        raise HTTPException(status_code=503, detail=f"Model error ({model}): {err_msg}")

    # Reflection pass — for complex tasks (code, research, plans) only
    if _SIGNAL_REFLECTION.search(user_message):
        assistant_content = await _reflect(user_message, assistant_content, ctx)

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

    # Guard against empty responses — don't poison the history with blank turns
    if not assistant_content or not assistant_content.strip():
        assistant_content = "I'm sorry, I didn't get a response. Please try again."
        # Drop the user message we just appended so the failed turn isn't stored
        history.pop()
        redis_client.set(session_key, json.dumps(history))
        return {"response": assistant_content, "model": model, "trace_id": trace_id}

    # Append assistant response and save full history
    history.append({"role": "assistant", "content": assistant_content})
    redis_client.set(session_key, json.dumps(history))

    return {"response": assistant_content, "model": model, "trace_id": trace_id}

@app.post("/chat/stream", dependencies=[Depends(_require_api_key)])
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint. Returns SSE with progress and token events.

    Event data format: JSON object with a "type" field:
      {"type": "status", "text": "Searching the web..."}  — skill executing
      {"type": "token",  "text": "..."}                   — response token
      {"type": "error",  "text": "..."}                   — error occurred
      {"type": "done",   "model": "...", "trace_id": "..."} — complete

    Security: identical policy enforcement as /chat. The web-ui channel is
    treated as private (requires X-Api-Key, same as telegram/cli).
    """
    user_id = request.user_id or "default"
    trace_id = tracing.new_trace(user_id=user_id, channel=request.channel or "")

    active_persona_name = request.persona or persona_registry.get_session(user_id) or "default"
    active_persona = persona_registry.get(active_persona_name) or persona_registry.get("default")

    session_key = (
        f"chat:{user_id}:{active_persona_name}"
        if active_persona_name != "default"
        else f"chat:{user_id}"
    )
    try:
        raw = redis_client.get(session_key)
        history = json.loads(raw) if raw else []
    except Exception:
        history = []

    if request.image_base64:
        ocr_text = await _ocr_image(request.image_base64)
        scan_block = f"[Scanned document]\n{ocr_text}\n[End scan]"
        user_message = (
            f"{scan_block}\n\n{request.message}"
            if request.message.strip()
            else f"{scan_block}\n\nPlease extract and log the expenses from this receipt."
        )
    else:
        if not request.message.strip():
            raise HTTPException(status_code=400, detail="message or image_base64 is required")
        user_message = request.message

    history.append({"role": "user", "content": user_message})

    loaded_identity = identity_module.load_identity()
    now = datetime.now(timezone.utc)
    date_line = f"Current date and time (UTC): {now.strftime('%A, %B %d, %Y %H:%M UTC')}"
    system_prompt = date_line + "\n\n" + identity_module.build_system_prompt(loaded_identity)

    memory_block = build_working_memory(user_id)
    if memory_block:
        system_prompt += "\n\n" + memory_block

    try:
        brain_block = await build_brain_context(request.message, channel=request.channel or "")
        if brain_block:
            system_prompt += "\n\n" + brain_block
    except Exception:
        pass

    if active_persona and active_persona.system_prompt_extra:
        system_prompt += f"\n\n{active_persona.system_prompt_extra}"

    _req_channel = request.channel or ""
    if _req_channel not in ("telegram", "cli", "mumble_owner", "web-ui"):
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
    if in_bootstrap and request.channel != "cli":
        raise HTTPException(
            status_code=403,
            detail="Bootstrap mode is active. Use 'agent bootstrap-reset' from the local machine CLI.",
        )

    if in_bootstrap:
        truncated = list(history)
    else:
        truncated = list(history)
        dropped = []
        while len(truncated) > 1 and sum(estimate_tokens(m["content"]) for m in truncated) > HISTORY_TOKEN_BUDGET:
            dropped.append(truncated.pop(0))
        if dropped:
            asyncio.create_task(_summarise_and_store(dropped, user_id))

    ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    model = route_model(user_message, request.model)
    if len(skill_registry) > 0 and request.model is None:
        lower = user_message.lower()
        if any(kw in lower for kw in CODING_KEYWORDS):
            model = CODING_MODEL
        else:
            model = TOOL_MODEL

    tracing.log_chat_request(user_message, model=model, bootstrap=in_bootstrap)

    ctx = DEEP_NUM_CTX if model in (DEEP_MODEL, CODING_MODEL) else NUM_CTX

    if active_persona and active_persona.allowed_skills is not None:
        from skills.registry import SkillRegistry as _SR
        effective_registry = _SR()
        for _sname in active_persona.allowed_skills:
            _s = skill_registry.get(_sname)
            if _s:
                effective_registry.register(_s)
        for _mgmt in ("list_personas", "switch_persona", "create_persona", "delete_persona"):
            _s = skill_registry.get(_mgmt)
            if _s and not effective_registry.get(_mgmt):
                effective_registry.register(_s)
    else:
        effective_registry = skill_registry

    # Narrow tool list to only skills relevant to this message (default persona only)
    if not (active_persona and active_persona.allowed_skills is not None):
        effective_registry = _build_focused_registry(user_message, effective_registry)

    tools = effective_registry.to_ollama_tools() or None

    if tools:
        _available = {s for s in effective_registry._skills}
        _skill_lines = "\n".join(
            v for k, v in _SKILL_INSTRUCTIONS.items() if k in _available
        )
        _tool_block = (
            "\n\n## Tool Usage\n"
            "You have real-time tools available. Follow these rules strictly:\n\n"
            "- You know the current date and time — it is given at the top of your context. "
            "Answer date/time questions directly and naturally. Never mention the system "
            "prompt, never say you lack real-time access to the date or time.\n"
        )
        if _skill_lines:
            _tool_block += _skill_lines
        system_prompt += _tool_block
        forcing = _tool_forcing_directive(user_message)
        if forcing:
            system_prompt += forcing

        _img_is_inventory = bool(
            re.search(r"\binventory\b|\bstock count\b|\bquantities\b|\bquantity on hand\b", user_message, re.IGNORECASE)
            or len(re.findall(r"\b(?:RAW|PKG|SP)-[A-Z]+\b", user_message)) >= 3
        )
        if request.image_base64 and "sp_costs" in _available and not _img_is_inventory:
            system_prompt += (
                "\n\n## Receipt Processing\n"
                "A scanned document has been injected into the user message above. "
                "If it contains purchase or expense data (receipt, invoice, or order confirmation), "
                "you MUST call `sp_costs` with action=log_expense to record it. "
                "Extract: vendor (→supplier field), date, total amount, and infer the category "
                "(ingredients/packaging/equipment/shipping/marketing/other based on vendor and items). "
                "After logging, confirm with a brief summary of what was recorded."
            )

    # Rebuild ollama_messages with updated system prompt (tool block was appended above)
    ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    # Two-pass planning: for multi-step requests, generate an execution plan first
    if _SIGNAL_MULTISTEP.search(user_message) and tools:
        _tool_names = [t["function"]["name"] for t in tools]
        _plan = await _generate_plan(user_message, _tool_names, ctx)
        if _plan:
            system_prompt += f"\n\n## Execution Plan\n{_plan}"
            ollama_messages = [{"role": "system", "content": system_prompt}] + truncated

    async def event_generator():
        status_queue: asyncio.Queue = asyncio.Queue()

        async def on_status(text: str) -> None:
            await status_queue.put(text)

        # Run tool context gathering as a background task so we can drain the
        # status queue concurrently and yield events to the client in real time.
        ctx_task = asyncio.create_task(
            gather_tool_context(
                ollama_client=ollama_client,
                messages=ollama_messages,
                tools=tools,
                model=model,
                ctx=ctx,
                skill_registry=effective_registry,
                policy_engine=policy_engine,
                approval_manager=approval_manager,
                auto_approve=request.auto_approve,
                user_id=user_id,
                max_iterations=MAX_TOOL_ITERATIONS,
                channel=request.channel or "",
                persona=active_persona_name,
                status_callback=on_status,
            )
        )

        # Yield status events while tool iterations run
        while not ctx_task.done():
            try:
                status_text = status_queue.get_nowait()
                yield {"data": json.dumps({"type": "status", "text": status_text})}
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)

        # Drain any status events that arrived in the final tick
        while not status_queue.empty():
            status_text = status_queue.get_nowait()
            yield {"data": json.dumps({"type": "status", "text": status_text})}

        try:
            synth_messages, tool_stats, precomputed = await ctx_task
        except Exception as exc:
            err = str(exc) or f"({type(exc).__name__})"
            yield {"data": json.dumps({"type": "error", "text": f"Model error: {err}"})}
            tracing._emit("chat", {"status": "error", "model": model, "error": err})
            return

        # ── Synthesis ────────────────────────────────────────────────────────
        # Three paths:
        #  A) No tools called → use precomputed text directly.
        #  B) Tools called + reflection needed → collect synthesis non-streaming,
        #     run reflection, yield the final text as one event.
        #  C) Tools called + no reflection → stream synthesis token by token.
        needs_reflection = bool(_SIGNAL_REFLECTION.search(user_message))
        no_tools = precomputed is not None and not tool_stats["skills_called"]

        if no_tools:
            # Path A
            draft = precomputed
        else:
            # Path B or C — need to synthesize
            if needs_reflection:
                # Collect synthesis without streaming so we can reflect on it
                try:
                    synth_resp = await ollama_client.chat(
                        model=model,
                        messages=synth_messages,
                        options={"num_ctx": ctx},
                    )
                    draft = (synth_resp.message.content or "").strip()
                except Exception as exc:
                    err = str(exc) or f"({type(exc).__name__})"
                    yield {"data": json.dumps({"type": "error", "text": f"Synthesis error: {err}"})}
                    tracing._emit("chat", {"status": "error", "model": model, "error": err})
                    return
            else:
                # Path C — stream tokens as they arrive
                draft = ""
                try:
                    stream = await ollama_client.chat(
                        model=model,
                        messages=synth_messages,
                        stream=True,
                        options={"num_ctx": ctx},
                    )
                    async for chunk in stream:
                        token = chunk.message.content or ""
                        if token:
                            draft += token
                            yield {"data": json.dumps({"type": "token", "text": token})}
                except Exception as exc:
                    err = str(exc) or f"({type(exc).__name__})"
                    yield {"data": json.dumps({"type": "error", "text": f"Synthesis error: {err}"})}
                    tracing._emit("chat", {"status": "error", "model": model, "error": err})
                    return

            if tool_stats.get("max_iterations_hit"):
                draft = f"[max iterations reached]\n{draft}"

        # Reflection pass — applies to paths A and B (path C already streamed)
        if needs_reflection:
            yield {"data": json.dumps({"type": "status", "text": "Reviewing response..."})}
            final_text = await _reflect(user_message, draft, ctx)
            yield {"data": json.dumps({"type": "token", "text": final_text})}
        elif no_tools:
            # Path A, no reflection — yield as single token
            final_text = draft
            yield {"data": json.dumps({"type": "token", "text": draft})}
        else:
            # Path C — already streamed token by token
            final_text = draft

        if not final_text or not final_text.strip():
            final_text = "I'm sorry, I didn't get a response. Please try again."

        # ── Persist history and trace ────────────────────────────────────────
        history.append({"role": "assistant", "content": final_text})
        try:
            redis_client.set(session_key, json.dumps(history))
        except Exception:
            pass

        tracing.log_chat_response(
            model=model,
            response_preview=final_text,
            eval_count=0,
            prompt_eval_count=0,
            total_duration_ms=0,
            tool_iterations=tool_stats["iterations"],
            skills_called=tool_stats["skills_called"],
        )

        yield {"data": json.dumps({"type": "done", "model": model, "trace_id": trace_id})}

    return EventSourceResponse(event_generator())


@app.get("/health")
async def health():
    return {"status": "healthy"}




@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-format metrics scrape endpoint. No auth — internal network only."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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


@app.get("/personas")
async def list_personas():
    """List all available personas."""
    personas = persona_registry.list_all()
    return {
        "personas": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "is_builtin": p.is_builtin,
                "allowed_skills": p.allowed_skills,
            }
            for p in personas
        ]
    }


@app.post("/persona/session", dependencies=[Depends(_require_api_key)])
async def set_persona_session(body: dict):
    """Set the active persona for a user. Body: {user_id, persona_name}."""
    user_id = body.get("user_id", "default")
    persona_name = body.get("persona_name", "default")
    persona = persona_registry.get(persona_name)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_name}' not found")
    persona_registry.set_session(user_id, persona_name)
    return {"user_id": user_id, "persona_name": persona_name, "display_name": persona.display_name}


@app.post("/policy/reload", dependencies=[Depends(_require_api_key)])
async def policy_reload():
    """Hot-reload policy.yaml without restarting the container."""
    app.state.policy_engine.load_config()
    return {"status": "reloaded"}

async def _update_gauges():
    """Background task: refresh gauge metrics from Redis every 15 seconds."""
    while True:
        try:
            metrics.queue_depth.set(redis_client.llen("queue:chat"))
            pending_keys = redis_client.keys("approval:*")
            count = sum(
                1 for k in pending_keys
                if redis_client.hget(k, "status") == "pending"
            )
            metrics.pending_approvals.set(count)
        except Exception:
            pass
        await asyncio.sleep(15)


@app.on_event("startup")
async def startup():
    # Wire runtime state for heartbeat job runner
    app.state.ollama_client = ollama_client
    app.state.skill_registry = skill_registry
    app.state.tool_model = TOOL_MODEL
    app.state.num_ctx = NUM_CTX
    app.state.max_tool_iterations = MAX_TOOL_ITERATIONS
    app.state.job_manager = JobManager(redis_client)
    seed_default_jobs(app.state.job_manager)
    start_heartbeat(app.state)
    asyncio.create_task(_update_gauges())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
