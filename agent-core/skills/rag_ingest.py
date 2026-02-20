"""
RAG ingest skill — adds text to the local ChromaDB vector store.

Uses ChromaDB's DefaultEmbeddingFunction (all-MiniLM-L6-v2 via sentence-transformers)
to generate embeddings client-side. This is the same embedding path used by
rag_search, ensuring query/document vector compatibility.
"""

import uuid
from typing import Any, Dict, List, Tuple

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel

MAX_TEXT_CHARS = 50_000
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping fixed-size chunks."""
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        if end == text_len:
            break
        start = end - overlap
    return chunks


class RagIngestSkill(SkillBase):
    """Add text content to the local ChromaDB knowledge base."""

    CHROMA_HOST = "chroma-rag"
    CHROMA_PORT = 8000
    COLLECTION_NAME = "rag_data"

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="rag_ingest",
            description=(
                "Add text content to the local knowledge base (ChromaDB) so it can be "
                "retrieved later via rag_search. Use this to store facts, documents, "
                "or notes that should persist across conversations."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="rag_ingest",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text content to add to the knowledge base.",
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "Optional label for where this content came from "
                            "(e.g. 'user note', 'web article', 'conversation summary')."
                        ),
                    },
                },
                "required": ["text"],
            },
        )

    def validate(self, params: Dict[str, Any]) -> Tuple[bool, str]:
        text = params.get("text", "")
        if not isinstance(text, str):
            return False, "Parameter 'text' must be a string"
        if not text.strip():
            return False, "Parameter 'text' must not be empty"
        if len(text) > MAX_TEXT_CHARS:
            return False, f"Parameter 'text' must be under {MAX_TEXT_CHARS} characters"
        source = params.get("source", "")
        if source and not isinstance(source, str):
            return False, "Parameter 'source' must be a string"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Chunk text and store in ChromaDB using DefaultEmbeddingFunction."""
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        text = params["text"]
        source = params.get("source", "agent")
        chunks = _chunk_text(text)
        ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = [{"source": source} for _ in chunks]

        try:
            ef = DefaultEmbeddingFunction()
            client = chromadb.HttpClient(host=self.CHROMA_HOST, port=self.CHROMA_PORT)
            collection = client.get_or_create_collection(
                self.COLLECTION_NAME, embedding_function=ef
            )
            collection.add(documents=chunks, ids=ids, metadatas=metadatas)
            return {"chunks_added": len(chunks), "source": source}
        except Exception as e:
            return {"error": str(e)}

    def sanitize_output(self, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"[rag_ingest] Failed to store in knowledge base: {result['error']}"
        if isinstance(result, dict):
            n = result.get("chunks_added", 0)
            src = result.get("source", "unknown")
            return f"Added {n} chunk(s) to knowledge base (source: {src})."
        return str(result)
