"""Metadata extraction via qwen3:8b — people, topics, action_items, type."""
import json
import os
import httpx

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
REASONING_MODEL = os.getenv("REASONING_MODEL", "qwen3:8b")

_PROMPT = """Extract structured metadata from this text. Return ONLY valid JSON:
{
  "people": ["list of names mentioned"],
  "action_items": ["list of tasks/action items"],
  "dates_mentioned": ["YYYY-MM-DD dates, convert relative dates"],
  "topics": ["1-3 topic tags, always at least 1"],
  "type": "observation|task|idea|reference|person_note"
}

Text: {text}

Return only the JSON object."""


async def extract_metadata(text: str) -> dict:
    """Extract structured metadata from thought content.

    Falls back to minimal metadata on any error — never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": REASONING_MODEL,
                    "messages": [{"role": "user", "content": _PROMPT.format(text=text[:1000])}],
                    "stream": False,
                    "options": {"num_ctx": 2048},
                    "think": False,
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                parts = content.split("```")
                content = parts[1] if len(parts) > 1 else content
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())
    except Exception:
        return {
            "people": [],
            "action_items": [],
            "dates_mentioned": [],
            "topics": ["note"],
            "type": "observation",
        }
