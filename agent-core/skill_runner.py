"""
Skill runner — policy-gated skill execution and Ollama tool loop.

Public entry points:
  execute_skill()        — run one skill through the full policy pipeline
  gather_tool_context()  — run all tool iterations, return messages ready for synthesis
                           plus an optional precomputed answer when no tools were needed
  run_tool_loop()        — full loop: gather_tool_context + batch synthesis (original API)

Neither function raises — all failures are returned as error strings so the
LLM can see what went wrong and decide how to proceed.
"""

import json
import re
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

# Phrases that indicate the model explicitly refused tool use due to perceived
# lack of real-time access. Kept as a fallback for cases where the user message
# doesn't contain real-time signals but the model still refuses.
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

_REALTIME_SIGNAL = re.compile(
    r"current|latest|recent|today|tonight|right now|live|"
    r"weather|forecast|temperature|"
    r"price|stock|crypto|bitcoin|"
    r"score|result|standings|match|game|"
    r"news|breaking|headline|"
    r"scrape|crawl|fetch.+url|"
    r"search for|look up|find out|check if|"
    r"who won|what happened|is .{1,30} open|when does|"
    r"who is (?:the |a )?(?:current )?(?:president|prime minister|ceo|head|"
    r"leader|governor|mayor|secretary|director|chancellor|king|queen|pope)|"
    r"who (?:leads|runs|heads|controls|governs|commands)\b|"
    r"who is in (?:charge|office|power)\b|"
    r"what is the (?:current |latest )?(?:status|state|situation|rate|level)\b|"
    r"is .{1,40} still\b|"
    r"has .{1,40} (?:changed|updated|happened)\b",
    re.IGNORECASE,
)

_RETRY_NUDGE = (
    "You have a web_search tool available. "
    "Please use it now to find a current answer rather than relying on training data."
)

# Human-readable status labels for each skill, shown to the user during streaming.
_SKILL_STATUS_LABELS: Dict[str, str] = {
    "web_search": "Searching the web",
    "url_fetch": "Fetching URL",
    "file_read": "Reading file",
    "file_write": "Writing file",
    "pdf_parse": "Parsing PDF",
    "rag_search": "Searching documents",
    "rag_ingest": "Indexing document",
    "remember": "Saving to memory",
    "recall": "Searching memory",
    "search_thoughts": "Searching brain memory",
    "capture_thought": "Saving to brain memory",
    "python_exec": "Running Python code",
    "calculate": "Calculating",
    "convert_units": "Converting units",
    "calendar_read": "Checking calendar",
    "calendar_write": "Updating calendar",
    "sp_inventory": "Checking inventory",
    "sp_orders": "Looking up orders",
    "sp_faq": "Searching FAQ",
    "sp_costs": "Checking expenses",
    "sp_time_log": "Logging time",
    "sp_recipes": "Looking up recipes",
    "sp_promotions": "Checking promotions",
    "create_task": "Scheduling task",
    "list_tasks": "Listing tasks",
    "cancel_task": "Cancelling task",
    "todo": "Updating to-do list",
    "shell_exec": "Running shell command",
    "github": "Accessing GitHub",
    "memory_capture": "Saving to memory",
    "memory_search": "Searching memory",
}


def _skill_status_text(skill_name: str) -> str:
    """Return a short human-readable status string for a skill being executed."""
    label = _SKILL_STATUS_LABELS.get(skill_name, f"Running {skill_name}")
    return f"{label}..."


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


import tracing
from approval import ApprovalManager
from policy import PolicyEngine
from skills.base import SkillBase
from skills.registry import SkillRegistry


async def execute_skill(
    skill: SkillBase,
    params: Dict[str, Any],
    policy_engine: PolicyEngine,
    approval_manager: ApprovalManager,
    auto_approve: bool,
    user_id: str,
    channel: str = "",
    persona: str = "default",
) -> str:
    """Run a skill through the full policy pipeline.

    Pipeline:
      0. Channel privacy gate (blocks personal-data skills on non-private channels)
      1. Rate-limit check
      2. Parameter validation
      3. Approval gate (if skill.requires_approval and not auto_approve)
      4. Execute with timing
      5. Sanitize output
      6. Trace the call

    Returns:
        Sanitized string output on success, or an error string on any failure.
        Never raises.
    """
    skill_name = skill.metadata.name
    status = "error"
    duration_ms: float = 0.0

    # 0. Channel privacy gate — hard block before any data is fetched
    if skill.metadata.private_channels and channel not in skill.metadata.private_channels:
        allowed = ", ".join(sorted(skill.metadata.private_channels))
        return (
            f"[{skill_name}] Personal data is only accessible on private channels "
            f"({allowed}). This request cannot be fulfilled on '{channel or 'unknown'}'."
        )

    # 1. Rate limit
    if not policy_engine.check_rate_limit(skill.metadata.rate_limit):
        status = "rate_limited"
        try:
            tracing.log_skill_call(
                skill_name=skill_name,
                params=params,
                status=status,
                duration_ms=0.0,
            )
        except Exception:
            pass
        return f"[{skill_name}] Rate limit reached — try again later."

    # 2. Validate
    try:
        ok, reason = skill.validate(params)
    except Exception as exc:
        return f"[{skill_name}] Validation error: {exc}"

    if not ok:
        return f"[{skill_name}] Invalid parameters: {reason}"

    # 3. Approval gate
    if skill.requires_approval and not auto_approve:
        try:
            description = f"Execute skill '{skill_name}' for user {user_id}"
            custom = await skill.pre_approval_description(params)
            if custom:
                description = custom
            approval_id = approval_manager.create_request(
                action=f"skill:{skill_name}",
                zone="external",
                risk_level=skill.metadata.risk_level.value,
                description=description,
                target=skill_name,
            )
            resolution = await approval_manager.wait_for_resolution(approval_id)
            if resolution != "approved":
                return f"[{skill_name}] Skill execution was not approved."
        except Exception as exc:
            return f"[{skill_name}] Approval error: {exc}"

    # 4. Execute with timing
    start_time = time.time()
    try:
        result = await skill.execute({**params, "_user_id": user_id, "_persona": persona})
        status = "success"
    except Exception as exc:
        duration_ms = (time.time() - start_time) * 1000
        try:
            tracing.log_skill_call(
                skill_name=skill_name,
                params=params,
                status="error",
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        return f"[{skill_name}] Execution error: {exc}"

    duration_ms = (time.time() - start_time) * 1000

    # 5. Sanitize output
    try:
        sanitized = skill.sanitize_output(result)
    except Exception as exc:
        try:
            tracing.log_skill_call(
                skill_name=skill_name,
                params=params,
                status="error",
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        return f"[{skill_name}] Output sanitization error: {exc}"

    # 6. Trace
    try:
        tracing.log_skill_call(
            skill_name=skill_name,
            params=params,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception:
        pass

    return sanitized


async def gather_tool_context(
    ollama_client: Any,
    messages: List[Dict],
    tools: Optional[List[Dict]],
    model: str,
    ctx: int,
    skill_registry: SkillRegistry,
    policy_engine: PolicyEngine,
    approval_manager: ApprovalManager,
    auto_approve: bool,
    user_id: str,
    max_iterations: int,
    channel: str = "",
    persona: str = "",
    status_callback: Optional[Callable[[str], Coroutine]] = None,
) -> Tuple[List[Dict], Dict, Optional[str]]:
    """Run tool-calling iterations and return context ready for synthesis.

    This is the core loop shared by both the batch and streaming chat endpoints.
    It handles tool dispatch, nudge retries, and per-skill call limits — but does
    NOT generate the final text response. That is left to the caller so it can
    choose between batch and streaming synthesis.

    Args:
        status_callback: Optional async callable invoked before each skill
            execution with a human-readable status string. Used by the streaming
            endpoint to emit progress events to the client.

    Returns:
        (messages, stats, precomputed_text) where:
          messages        — accumulated context ready to pass to the synthesis call
          stats           — {"iterations": int, "skills_called": List[str],
                             "max_iterations_hit": bool}
          precomputed_text — the model's response text when it answered directly
                             without calling any tools (or after a nudge retry).
                             None when tools were called — caller must synthesize
                             over the accumulated tool context.

    The distinction matters for streaming: when precomputed_text is not None and
    no skills were called, the caller can yield it immediately without an extra
    model call. When skills were called, a fresh streaming synthesis call gives
    the user real-time token delivery over the tool results.
    """
    options = {"num_ctx": ctx}

    # No tools registered — one batch call, return result as precomputed.
    if not tools:
        response = await ollama_client.chat(model=model, messages=messages, options=options)
        text = _msg_content(response.message)
        return messages, {"iterations": 0, "skills_called": [], "max_iterations_hit": False}, text

    per_skill_counts: Dict[str, int] = {}
    skills_called: List[str] = []
    iteration = 0

    while iteration < max_iterations:
        response = await ollama_client.chat(
            model=model,
            messages=messages,
            tools=tools,
            options=options,
            think=False,
        )
        msg = response.message
        tool_calls = msg.tool_calls or []

        # ── No tool calls proposed ────────────────────────────────────────────
        if not tool_calls:
            text = _msg_content(msg)

            last_user_msg = next(
                (m.get("content", "") for m in reversed(messages) if m["role"] == "user"),
                "",
            )
            should_nudge = _REFUSAL_PATTERN.search(text) or _REALTIME_SIGNAL.search(last_user_msg)
            if iteration == 0 and not skills_called and should_nudge:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": _RETRY_NUDGE},
                ]
                iteration += 1
                continue

            # Model answered directly — return text as precomputed so the caller
            # can use it without an extra synthesis call.
            stats = {
                "iterations": iteration,
                "skills_called": skills_called,
                "max_iterations_hit": False,
            }
            return messages, stats, text

        # ── Execute tool calls ───────────────────────────────────────────────
        messages = messages + [_msg_to_dict(msg)]

        for tool_call in tool_calls:
            fn = tool_call.function
            name = fn.name
            raw_args = fn.arguments

            if isinstance(raw_args, str):
                try:
                    params = json.loads(raw_args)
                except (json.JSONDecodeError, ValueError):
                    params = {}
            else:
                params = raw_args if isinstance(raw_args, dict) else {}

            skill = skill_registry.get(name)
            if skill is None:
                tool_result = f"[{name}] Unknown skill — not registered."
            else:
                count = per_skill_counts.get(skill.name, 0)
                if count >= skill.metadata.max_calls_per_turn:
                    tool_result = (
                        f"[{skill.name}] Per-turn call limit "
                        f"({skill.metadata.max_calls_per_turn}) reached — "
                        "try a different approach."
                    )
                else:
                    per_skill_counts[skill.name] = count + 1
                    skills_called.append(skill.name)
                    if status_callback is not None:
                        try:
                            await status_callback(_skill_status_text(skill.name))
                        except Exception:
                            pass
                    tool_result = await execute_skill(
                        skill=skill,
                        params=params,
                        policy_engine=policy_engine,
                        approval_manager=approval_manager,
                        auto_approve=auto_approve,
                        user_id=user_id,
                        channel=channel,
                        persona=persona,
                    )

            messages = messages + [{"role": "tool", "content": tool_result}]

        iteration += 1

    # Max iterations reached — append synthesis request; caller must generate answer.
    messages = messages + [
        {
            "role": "user",
            "content": "Please provide your final answer based on the information gathered so far.",
        }
    ]
    stats = {
        "iterations": iteration,
        "skills_called": skills_called,
        "max_iterations_hit": True,
    }
    return messages, stats, None  # precomputed = None; caller must synthesize


async def run_tool_loop(
    ollama_client: Any,
    messages: List[Dict],
    tools: Optional[List[Dict]],
    model: str,
    ctx: int,
    skill_registry: SkillRegistry,
    policy_engine: PolicyEngine,
    approval_manager: ApprovalManager,
    auto_approve: bool,
    user_id: str,
    max_iterations: int,
    channel: str = "",
    persona: str = "",
) -> Tuple[str, List[Dict], Dict]:
    """Drive the Ollama tool-calling loop (batch mode).

    Wraps gather_tool_context() and performs the final batch synthesis call
    when tools were used. When no tools were called, the precomputed answer
    from gather_tool_context is returned directly (no extra model call).

    Returns:
        (final_text, updated_messages, stats)
    """
    messages, stats, precomputed = await gather_tool_context(
        ollama_client=ollama_client,
        messages=messages,
        tools=tools,
        model=model,
        ctx=ctx,
        skill_registry=skill_registry,
        policy_engine=policy_engine,
        approval_manager=approval_manager,
        auto_approve=auto_approve,
        user_id=user_id,
        max_iterations=max_iterations,
        channel=channel,
        persona=persona,
    )

    options = {"num_ctx": ctx}

    if precomputed is not None:
        # Model answered without tools, or last tool-decision pass returned text.
        text = precomputed
    else:
        # Tools were used or max iterations hit — synthesize over accumulated context.
        response = await ollama_client.chat(model=model, messages=messages, options=options)
        text = _msg_content(response.message)
        if stats.get("max_iterations_hit"):
            text = f"[max iterations reached]\n{text}"

    return text, messages + [{"role": "assistant", "content": text}], stats
