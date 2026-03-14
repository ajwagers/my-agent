"""nomic-embed-text embeddings via Ollama /api/embed."""
import os
import httpx

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-runner:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


async def embed(text: str) -> list[float]:
    """Return 768-dim embedding for text. Raises on Ollama failure."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]


def vec_to_str(embedding: list[float]) -> str:
    """Format embedding list as pgvector literal string."""
    return "[" + ",".join(str(x) for x in embedding) + "]"
