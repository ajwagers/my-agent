"""
Skill runner — policy-gated skill execution and Ollama tool loop.

Two public entry points:
  execute_skill()   — run one skill through the full policy pipeline
  run_tool_loop()   — drive the Ollama tool-calling loop until the model
                      stops requesting tools or max_iterations is reached

Neither function raises — all failures are returned as error strings so the
LLM can see what went wrong and decide how to proceed.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
    # Patterns for models that answer confidently from stale training data
    # without explicitly refusing — e.g. qwen3 saying it was "developed prior
    # to April 2023" or referring to "my last update".
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

# Keywords in the *user's* message that indicate real-time data is likely needed.
# Checking the input is more robust than matching the model's refusal phrasing —
# the nudge fires whether the model explicitly refuses, silently answers from
# training data, or uses any phrasing not listed in _REFUSAL_PATTERN.
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

_RETRY_NUDGE = (
    "You have a web_search tool available. "
    "Please use it now to find a current answer rather than relying on training data."
)

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
    # Inject context for skills that need user scoping (e.g. remember, recall).
    # Done after validation so _user_id doesn't interfere with param checks.
    start_time = time.time()
    try:
        result = await skill.execute({**params, "_user_id": user_id})
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
) -> Tuple[str, List[Dict], Dict]:
    """Drive the Ollama tool-calling loop.

    Single-model loop: `model` handles dispatch, tool calls, and synthesis.
    On the first iteration, if the model produces no tool calls and the user
    message contains real-time signals (or the model explicitly refuses to
    search), one nudge is injected and the loop retries.

    Returns:
        (final_text, updated_messages, stats) where:
          final_text       — the model's last text response
          updated_messages — messages list with all tool turns appended
          stats            — {"iterations": int, "skills_called": List[str]}
    """
    options = {"num_ctx": ctx}

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

    # No tools — straight to model, no loop needed
    if not tools:
        response = await ollama_client.chat(model=model, messages=messages, options=options)
        text = _msg_content(response.message)
        messages = messages + [{"role": "assistant", "content": text}]
        return text, messages, {"iterations": 0, "skills_called": []}

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

        # ── No tool calls proposed ──────────────────────────────────────────
        if not tool_calls:
            text = _msg_content(msg)

            # Nudge once on the first pass if real-time signals or explicit
            # refusal phrasing detected and no skills have run yet.
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

            messages = messages + [{"role": "assistant", "content": text}]
            return text, messages, {"iterations": iteration, "skills_called": skills_called}

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
                    tool_result = await execute_skill(
                        skill=skill,
                        params=params,
                        policy_engine=policy_engine,
                        approval_manager=approval_manager,
                        auto_approve=auto_approve,
                        user_id=user_id,
                        channel=channel,
                    )

            messages = messages + [{"role": "tool", "content": tool_result}]

        iteration += 1

    # Max iterations reached — synthesis model produces the final answer
    messages = messages + [
        {
            "role": "user",
            "content": "Please provide your final answer based on the information gathered so far.",
        }
    ]
    response = await ollama_client.chat(model=model, messages=messages, options=options)
    text = _msg_content(response.message)
    messages = messages + [{"role": "assistant", "content": text}]
    return (
        f"[max iterations reached]\n{text}",
        messages,
        {"iterations": iteration, "skills_called": skills_called},
    )
