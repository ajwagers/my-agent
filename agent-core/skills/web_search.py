"""
Web search skill — Brave Search primary, Tavily fallback.

Routing:
  - Query contains a URL (https?://) → Brave standard web search
    (returns title + description per result, good for URL-specific lookups)
  - All other queries → Brave LLM Context
    (returns pre-extracted text chunks from each page, no extra fetch needed)
  - Any Brave failure → Tavily fallback

Both API keys are fetched via secret_broker at execution time — never
exposed to the LLM.  Brave key: BRAVE_SEARCH_API_KEY.
Tavily key: TAVILY_API_KEY.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from policy import RiskLevel
from skills.base import SkillBase, SkillMetadata


# ---------------------------------------------------------------------------
# Sanitization — applied to all text before it re-enters LLM context
# ---------------------------------------------------------------------------

_SUSPICIOUS_PATTERN = re.compile(
    r"<[^>]+>"                        # HTML tags
    r"|javascript:"                   # javascript: URIs
    r"|data:"                         # data: URIs
    r"|ignore\s+previous"             # prompt injection phrase
    r"|system\s+prompt"               # prompt injection phrase
    r"|disregard\s+instructions",     # prompt injection phrase
    re.IGNORECASE,
)

# Detects a bare URL in the search query → use standard web search
_URL_IN_QUERY = re.compile(r"https?://", re.IGNORECASE)

# ---------------------------------------------------------------------------
# API endpoints and limits
# ---------------------------------------------------------------------------

_BRAVE_LLM_URL = "https://api.search.brave.com/res/v1/llm/context"
_BRAVE_WEB_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_URL    = "https://api.tavily.com/search"

_MAX_RESULTS        = 5
_LLM_MAX_TOKENS     = 4096   # token budget for Brave LLM Context response
_TOTAL_OUTPUT_CAP   = 5000   # chars — prevents context bloat in system prompt
_SNIPPET_CAP        = 1000   # chars per result for standard/Tavily mode


def _clean(text: str) -> str:
    """Strip suspicious patterns and return stripped text."""
    return _SUSPICIOUS_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# Backend helpers — each returns a normalised dict or raises on failure
# ---------------------------------------------------------------------------

def _brave_llm_context(api_key: str, query: str) -> Dict:
    """
    Call Brave LLM Context endpoint.
    Returns normalised {"_source": "brave_llm", "items": [...]} dict.
    Raises requests.exceptions.RequestException on network/HTTP errors.
    """
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    params = {
        "q": query,
        "count": _MAX_RESULTS,
        "maximum_number_of_tokens": _LLM_MAX_TOKENS,
        "context_threshold_mode": "balanced",
    }
    resp = requests.get(_BRAVE_LLM_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    items = []
    for entry in data.get("grounding", {}).get("generic", [])[:_MAX_RESULTS]:
        title = _clean(str(entry.get("title", "")))
        url   = entry.get("url", "")
        # snippets is a list of text chunks — join them
        raw_snippets = entry.get("snippets", [])
        text = _clean(" ".join(str(s) for s in raw_snippets if isinstance(s, str)))
        if text:
            items.append({"title": title, "url": url, "text": text})

    return {"_source": "brave_llm", "items": items}


def _brave_web_search(api_key: str, query: str) -> Dict:
    """
    Call Brave standard web search endpoint.
    Returns normalised {"_source": "brave_web", "items": [...]} dict.
    Raises requests.exceptions.RequestException on network/HTTP errors.
    """
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    params = {
        "q": query,
        "count": _MAX_RESULTS,
        "extra_snippets": "true",
    }
    resp = requests.get(_BRAVE_WEB_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    items = []
    for entry in data.get("web", {}).get("results", [])[:_MAX_RESULTS]:
        title = _clean(str(entry.get("title", "")))
        url   = entry.get("url", "")
        parts = [_clean(str(entry.get("description", "")))]
        for s in entry.get("extra_snippets", []):
            parts.append(_clean(str(s)))
        text = " ".join(p for p in parts if p)
        if text:
            items.append({"title": title, "url": url, "text": text})

    return {"_source": "brave_web", "items": items}


def _tavily_search(api_key: str, query: str) -> Dict:
    """
    Call Tavily search API.
    Returns normalised {"_source": "tavily", "items": [...]} dict.
    Raises requests.exceptions.RequestException on network/HTTP errors.
    """
    resp = requests.post(
        _TAVILY_URL,
        json={
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": _MAX_RESULTS,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for entry in data.get("results", [])[:_MAX_RESULTS]:
        title = _clean(str(entry.get("title", "")))
        url   = entry.get("url", "")
        text  = _clean(str(entry.get("content", "")))
        if text:
            items.append({"title": title, "url": url, "text": text})

    return {"_source": "tavily", "items": items}


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

class WebSearchSkill(SkillBase):
    """Search the web — Brave LLM Context primary, Tavily fallback."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="web_search",
            description=(
                "Search the web for real-time information. "
                "Call this tool when asked about: current events, breaking news, "
                "sports scores or results, stock prices, weather, recently released "
                "software or products, or any fact that may have changed since 2024. "
                "Do not answer from training data for these topics — search instead."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="web_search",
            requires_approval=False,
            max_calls_per_turn=3,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The web search query.",
                    }
                },
                "required": ["query"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        query = params.get("query", "")
        if not isinstance(query, str):
            return False, "Parameter 'query' must be a string"
        if not query.strip():
            return False, "Parameter 'query' must not be empty"
        if len(query) > 500:
            return False, "Parameter 'query' must be under 500 characters"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Any:
        from secret_broker import get as get_secret

        query = params["query"].strip()
        use_url_mode = bool(_URL_IN_QUERY.search(query))

        # --- Brave (primary) ---
        brave_error: Optional[str] = None
        try:
            brave_key = get_secret("BRAVE_SEARCH_API_KEY")
            if use_url_mode:
                return _brave_web_search(brave_key, query)
            else:
                return _brave_llm_context(brave_key, query)
        except RuntimeError as exc:
            # Secret not set — skip Brave entirely
            brave_error = str(exc)
        except requests.exceptions.Timeout:
            brave_error = "Brave search timed out"
        except requests.exceptions.RequestException as exc:
            brave_error = f"Brave search failed: {exc}"
        except Exception as exc:
            brave_error = f"Brave search error: {exc}"

        # --- Tavily (fallback) ---
        try:
            tavily_key = get_secret("TAVILY_API_KEY")
            result = _tavily_search(tavily_key, query)
            # Tag that we fell back so it's visible in traces
            result["_brave_error"] = brave_error
            return result
        except RuntimeError as exc:
            return {"error": f"All search backends unavailable. Brave: {brave_error}. Tavily: {exc}"}
        except requests.exceptions.Timeout:
            return {"error": f"All search backends timed out. Brave: {brave_error}"}
        except requests.exceptions.RequestException as exc:
            return {"error": f"All search backends failed. Brave: {brave_error}. Tavily: {exc}"}
        except Exception as exc:
            return {"error": f"Search error. Brave: {brave_error}. Tavily: {exc}"}

    def sanitize_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return "No search results."
        if "error" in result:
            return f"Web search unavailable: {result['error']}"

        items: List[Dict] = result.get("items", [])
        if not items:
            return "No search results found."

        source = result.get("_source", "")
        per_item_cap = _SNIPPET_CAP if source in ("brave_web", "tavily") else _TOTAL_OUTPUT_CAP // max(len(items), 1)

        snippets = []
        for item in items:
            title = item.get("title", "").strip()
            url   = item.get("url", "").strip()
            text  = item.get("text", "").strip()

            if len(text) > per_item_cap:
                text = text[:per_item_cap] + " [truncated]"

            header = f"**{title}**" if title else ""
            if url:
                header = f"{header} ({url})" if header else url
            snippet = f"{header}\n{text}" if header else text

            if snippet.strip():
                snippets.append(snippet)

        if not snippets:
            return "No usable search results found."

        output = "\n\n---\n\n".join(snippets)
        if len(output) > _TOTAL_OUTPUT_CAP:
            output = output[:_TOTAL_OUTPUT_CAP] + "\n\n[results truncated]"
        return output
