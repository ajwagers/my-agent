"""
HTTP health probes for the agent stack services.

Each probe returns (status, details) where:
  status:  "healthy", "unhealthy", or "unknown"
  details: dict with service-specific info (models, memory, error, etc.)
"""

import requests

PROBE_TIMEOUT = 3  # seconds


def check_agent_core():
    """Probe agent-core /health endpoint."""
    try:
        r = requests.get("http://agent-core:8000/health", timeout=PROBE_TIMEOUT)
        if r.status_code == 200:
            return ("healthy", r.json())
        return ("unhealthy", {"status_code": r.status_code})
    except Exception as e:
        return ("unhealthy", {"error": str(e)})


def check_ollama():
    """Probe Ollama /api/tags for loaded models."""
    try:
        r = requests.get(
            "http://ollama-runner:11434/api/tags", timeout=PROBE_TIMEOUT
        )
        if r.status_code == 200:
            data = r.json()
            models = [m.get("name", "?") for m in data.get("models", [])]
            return ("healthy", {"models": models})
        return ("unhealthy", {"status_code": r.status_code})
    except Exception as e:
        return ("unhealthy", {"error": str(e)})


def check_chromadb():
    """Probe ChromaDB heartbeat endpoint (v2 API, falling back to v1)."""
    base = "http://chroma-rag:8000"
    try:
        # Try v2 first (chromadb >= 1.x), fall back to v1
        for version in ("v2", "v1"):
            r = requests.get(
                f"{base}/api/{version}/heartbeat", timeout=PROBE_TIMEOUT
            )
            if r.status_code == 200:
                return ("healthy", {"api": version})
        return ("unhealthy", {"status_code": r.status_code})
    except Exception as e:
        return ("unhealthy", {"error": str(e)})


def check_redis(redis_client):
    """Probe Redis via ping and memory info."""
    try:
        redis_client.ping()
        info = redis_client.info("memory")
        return ("healthy", {"memory": info.get("used_memory_human", "?")})
    except Exception as e:
        return ("unhealthy", {"error": str(e)})


def check_web_ui():
    """Probe Streamlit web-ui health endpoint."""
    try:
        r = requests.get(
            "http://web-ui:8501/_stcore/health", timeout=PROBE_TIMEOUT
        )
        if r.status_code == 200:
            return ("healthy", {})
        return ("unhealthy", {"status_code": r.status_code})
    except Exception as e:
        return ("unhealthy", {"error": str(e)})


def check_telegram_gateway():
    """No HTTP health endpoint available for telegram-gateway."""
    return ("unknown", {"note": "No health endpoint"})


def check_all(redis_client):
    """Run all health probes. Returns dict of service -> (status, details)."""
    return {
        "agent_core": check_agent_core(),
        "ollama": check_ollama(),
        "chromadb": check_chromadb(),
        "redis": check_redis(redis_client),
        "web_ui": check_web_ui(),
        "telegram_gateway": check_telegram_gateway(),
    }
