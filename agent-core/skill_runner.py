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

# Phrases that indicate the model refused to use tools due to perceived lack
# of real-time access. Detected to trigger a one-shot retry nudge.
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
    r"|don.t have access to current",
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
) -> str:
    """Run a skill through the full policy pipeline.

    Pipeline:
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
            approval_id = approval_manager.create_request(
                action=f"skill:{skill_name}",
                zone="external",
                risk_level=skill.metadata.risk_level.value,
                description=f"Execute skill '{skill_name}' for user {user_id}",
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
) -> Tuple[str, List[Dict], Dict]:
    """Drive the Ollama tool-calling loop.

    Args:
        ollama_client:    Ollama client instance (ollama.Client or compatible).
        messages:         Conversation history in Ollama format.
        tools:            List of tool dicts (from registry.to_ollama_tools())
                          or None to skip tool calling entirely.
        model:            Ollama model name to use.
        ctx:              num_ctx option value.
        skill_registry:   Registry to look up skills by name.
        policy_engine:    For rate-limit checks inside execute_skill.
        approval_manager: For approval gates inside execute_skill.
        auto_approve:     Whether to skip approval gates.
        user_id:          User identifier for approval requests.
        max_iterations:   Hard cap on tool-call rounds before forcing a final answer.

    Returns:
        (final_text, updated_messages, stats) where:
          final_text       — the model's last text response
          updated_messages — messages list with all tool turns appended
                             (use for Ollama context; do NOT save to Redis history)
          stats            — {"iterations": int, "skills_called": List[str]}
    """
    options = {"num_ctx": ctx}

    # No tools — plain chat, no loop needed
    if not tools:
        response = ollama_client.chat(model=model, messages=messages, options=options)
        text = response["message"].get("content", "")
        messages = messages + [{"role": "assistant", "content": text}]
        return text, messages, {"iterations": 0, "skills_called": []}

    per_skill_counts: Dict[str, int] = {}
    skills_called: List[str] = []
    iteration = 0

    while iteration < max_iterations:
        response = ollama_client.chat(
            model=model,
            messages=messages,
            tools=tools,
            options=options,
        )
        msg = response["message"]
        tool_calls = msg.get("tool_calls") or []

        # No tool calls — model produced its final text answer
        if not tool_calls:
            text = msg.get("content", "")

            # Auto-retry: if the model refused to use tools on its first attempt
            # due to perceived lack of real-time access, nudge it once.
            if iteration == 0 and not skills_called and _REFUSAL_PATTERN.search(text):
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": _RETRY_NUDGE},
                ]
                iteration += 1
                continue

            messages = messages + [{"role": "assistant", "content": text}]
            return text, messages, {"iterations": iteration, "skills_called": skills_called}

        # Append the assistant message (which contains tool_calls).
        # Always include role so downstream callers can filter by role reliably.
        messages = messages + [{**msg, "role": "assistant"}]

        # Execute each requested tool call
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})

            # arguments may arrive as a JSON string or already a dict
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
                    )

            messages = messages + [{"role": "tool", "content": tool_result}]

        iteration += 1

    # Max iterations reached — ask the model for a final answer with what it has
    messages = messages + [
        {
            "role": "user",
            "content": "Please provide your final answer based on the information gathered so far.",
        }
    ]
    response = ollama_client.chat(model=model, messages=messages, options=options)
    text = response["message"].get("content", "")
    messages = messages + [{"role": "assistant", "content": text}]
    return (
        f"[max iterations reached]\n{text}",
        messages,
        {"iterations": iteration, "skills_called": skills_called},
    )
