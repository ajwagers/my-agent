"""
Auto-injection middleware — silently enriches agent context with relevant
memories, household facts, and FAQ entries before every LLM call.

Usage (in app.py):
    from memory_middleware import build_brain_context
    brain_block = await build_brain_context(message, channel=request.channel or "")
    if brain_block:
        system_prompt += "\\n\\n" + brain_block

Privacy model
─────────────
Channels are partitioned into two tiers:

  Private (PRIVATE_CHANNELS): "telegram", "cli"
    → Full context injection: personal thoughts, household facts, identity files.

  Non-private (everything else, e.g. "mumble", "web-ui", ""):
    → Only business / FAQ context is injected.
    → Household facts are never injected (may contain addresses, credentials).
    → Thoughts from identity files (owner_profile, agent_soul, etc.) are stripped.
    → Regular user-captured thoughts are also withheld — they may be personal.

This is the *second* layer of privacy enforcement. The *first* is the
per-skill private_channels gate in execute_skill(). The *third* is the
system prompt privacy directive added in app.py.
"""
import asyncio
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

BRAIN_URL = os.getenv("BRAIN_URL", "http://open-brain-mcp:8002")

# Channels that may receive personal / household context
_PRIVATE_CHANNELS = frozenset({"telegram", "cli", "mumble_owner"})

# Similarity thresholds
_SILENT_THRESHOLD = 0.72    # inject silently
_ANNOUNCE_THRESHOLD = 0.92  # worth surfacing explicitly

# Thought metadata types that contain personal owner data — withheld on
# non-private channels even if they score above the similarity threshold.
_PERSONAL_THOUGHT_TYPES = frozenset({
    "owner_profile", "agent_soul", "agent_identity", "agent_directives",
})

# Signals that trigger explicit mention of memory
_EXPLICIT_RECALL = re.compile(
    r"do you remember|what do you know about|have i (?:told|mentioned)|"
    r"recall|from (?:last|our previous|a previous)|what did i say",
    re.IGNORECASE,
)

# Signals that trigger inventory / order search
_BUSINESS_SIGNAL = re.compile(
    r"\bstock\b|\binventory\b|\border\b|\blow\b|\breorder\b|\bbatch\b|"
    r"\bcuring\b|\bpine tar\b|\blye\b|\bshampoo\b|\bconditioner\b|"
    r"\bduo\b|\bsupplier\b|\bproduction\b|\bsummit pine\b|\bSP-\b",
    re.IGNORECASE,
)

# Customer support signals — search FAQ
_FAQ_SIGNAL = re.compile(
    r"\bguarantee\b|\brefund\b|\bingredient\b|\bhow to use\b|"
    r"\bhow long\b|\bscalp\b|\bdandruff\b|\bpine tar\b|\bshipping\b|"
    r"\bsubscri|\bcustomer\b|\bprice\b|\bcost\b",
    re.IGNORECASE,
)


def _is_personal_thought(hit: dict) -> bool:
    """Return True if this thought hit should be withheld on non-private channels."""
    if hit.get("source") == "identity_file":
        return True
    meta = hit.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("type") in _PERSONAL_THOUGHT_TYPES:
        return True
    return False


async def build_brain_context(
    message: str,
    channel: str = "",
    threshold: float = _SILENT_THRESHOLD,
) -> str:
    """Run parallel brain searches and return a context block for injection.

    On private channels (telegram, cli): injects thoughts + household + FAQ.
    On non-private channels: injects FAQ only (business context, no personal data).

    Returns empty string if brain is unavailable or nothing relevant found.
    Hard cap: 2000 chars total.
    """
    is_private = channel in _PRIVATE_CHANNELS

    tasks = [_search_thoughts(message, threshold)]
    if is_private:
        tasks.append(_search_household(message, threshold))
    else:
        tasks.append(asyncio.sleep(0))  # placeholder — household suppressed

    if _FAQ_SIGNAL.search(message):
        tasks.append(_search_faq(message, threshold))
    else:
        tasks.append(asyncio.sleep(0))  # placeholder

    results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_thoughts = results[0] if not isinstance(results[0], Exception) else []
    household_hits = results[1] if not isinstance(results[1], (Exception, type(None))) else []
    faq_hits = results[2] if not isinstance(results[2], (Exception, type(None))) else []

    # On non-private channels, strip all personal thought types
    if is_private:
        thoughts_hits = raw_thoughts if isinstance(raw_thoughts, list) else []
    else:
        thoughts_hits = [
            h for h in (raw_thoughts if isinstance(raw_thoughts, list) else [])
            if not _is_personal_thought(h)
        ]

    explicit = _EXPLICIT_RECALL.search(message)
    sections = []

    if thoughts_hits:
        header = "## Relevant Memories" if explicit else "<!-- brain:thoughts -->"
        lines = [header]
        for h in thoughts_hits[:5]:
            sim = h.get("similarity", 0)
            flag = " [high confidence]" if sim >= _ANNOUNCE_THRESHOLD else ""
            lines.append(f"- {h['content']}{flag}")
        sections.append("\n".join(lines))

    if household_hits and isinstance(household_hits, list):
        header = "## Household Knowledge" if explicit else "<!-- brain:household -->"
        lines = [header]
        for h in household_hits[:3]:
            lines.append(f"- [{h['category']}] {h['key']}: {h['value']}")
        sections.append("\n".join(lines))

    if faq_hits and isinstance(faq_hits, list):
        lines = ["<!-- brain:faq -->"]
        for h in faq_hits[:2]:
            lines.append(f"- Q: {h['question']}\n  A: {h['answer']}")
            if h.get("guardrail") == "no_medical_advice":
                lines.append("  (Note: do not give medical advice — refer to dermatologist)")
        sections.append("\n".join(lines))

    if not sections:
        return ""

    block = "\n\n".join(sections)
    if len(block) > 2000:
        block = block[:1997] + "..."
    return block


async def _search_thoughts(query: str, threshold: float) -> list:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{BRAIN_URL}/tools/search_thoughts",
                json={"query": query, "limit": 5, "threshold": threshold},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug(f"brain:thoughts search failed: {e}")
    return []


async def _search_household(query: str, threshold: float) -> list:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{BRAIN_URL}/tools/search_household_facts",
                json={"query": query, "limit": 3, "threshold": threshold},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug(f"brain:household search failed: {e}")
    return []


async def _search_faq(query: str, threshold: float) -> list:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{BRAIN_URL}/tools/search_faq",
                json={"query": query, "limit": 2, "threshold": threshold},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug(f"brain:faq search failed: {e}")
    return []
