"""
Web search skill — queries the Tavily REST API.

Provides the LLM with live web search capability. The API key is never
exposed to the LLM; it is fetched from the environment at execution time
via secret_broker.
"""

import re
from typing import Any, Dict, List, Tuple

import requests

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel


# Patterns stripped from web content before it re-enters LLM context.
# Guards against HTML injection and basic prompt injection via search results.
_SUSPICIOUS_PATTERN = re.compile(
    r"<[^>]+>"                        # HTML tags
    r"|javascript:"                   # javascript: URIs
    r"|data:"                         # data: URIs
    r"|ignore\s+previous"             # prompt injection phrase
    r"|system\s+prompt"               # prompt injection phrase
    r"|disregard\s+instructions",     # prompt injection phrase
    re.IGNORECASE,
)

_TAVILY_URL = "https://api.tavily.com/search"
_MAX_RESULTS = 5
_SNIPPET_MAX_CHARS = 1000


class WebSearchSkill(SkillBase):
    """Search the web via the Tavily API."""

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
        """Call Tavily search API and return the raw response dict."""
        from secret_broker import get as get_secret

        query = params["query"]
        try:
            api_key = get_secret("TAVILY_API_KEY")
        except RuntimeError as exc:
            return {"error": str(exc)}

        try:
            response = requests.post(
                _TAVILY_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": _MAX_RESULTS,
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": "Web search timed out."}
        except requests.exceptions.RequestException as exc:
            return {"error": f"Web search request failed: {exc}"}
        except Exception as exc:
            return {"error": f"Web search error: {exc}"}

    def sanitize_output(self, result: Any) -> str:
        """Extract and sanitize up to 5 search results."""
        if not isinstance(result, dict):
            return "No search results."

        if "error" in result:
            return f"Web search unavailable: {result['error']}"

        raw_results: List[Dict] = result.get("results", [])
        if not raw_results:
            return "No search results found."

        snippets: List[str] = []
        for item in raw_results[:_MAX_RESULTS]:
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()

            # Strip suspicious patterns from both title and content
            title = _SUSPICIOUS_PATTERN.sub("", title).strip()
            content = _SUSPICIOUS_PATTERN.sub("", content).strip()

            snippet = f"**{title}**\n{content}" if title else content

            # Cap each result individually to ensure breadth across sources
            if len(snippet) > _SNIPPET_MAX_CHARS:
                snippet = snippet[:_SNIPPET_MAX_CHARS] + " [truncated]"

            if snippet.strip():
                snippets.append(snippet)

        if not snippets:
            return "No usable search results found."

        return "\n\n---\n\n".join(snippets)
