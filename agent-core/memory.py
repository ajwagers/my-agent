"""
Long-term memory store — ChromaDB wrapper for the agent_memory collection.

Separate from rag_data so agent memories don't pollute the user knowledge base.

Metadata schema per entry:
  user_id   — scope memories per user
  type      — "fact" | "observation" | "preference" | "summary"
  source    — "agent" | "user"
  timestamp — unix timestamp (recency sort + age display)
"""

import os
import time
import uuid
from typing import Dict, List

CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma-rag")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
MEMORY_COLLECTION = "agent_memory"

VALID_TYPES = {"fact", "observation", "preference", "summary"}


class MemoryStore:
    """ChromaDB-backed long-term memory for the agent."""

    def __init__(
        self,
        host: str = CHROMA_HOST,
        port: int = CHROMA_PORT,
        collection_name: str = MEMORY_COLLECTION,
    ):
        self._host = host
        self._port = port
        self._collection_name = collection_name

    def _get_collection(self):
        """Connect and return the ChromaDB collection (lazy, per-call)."""
        import chromadb
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

        ef = OllamaEmbeddingFunction(
            url=os.getenv("OLLAMA_HOST", "http://ollama-runner:11434"),
            model_name=os.getenv("EMBED_MODEL", "nomic-embed-text"),
        )
        client = chromadb.HttpClient(host=self._host, port=self._port)
        return client.get_or_create_collection(
            self._collection_name, embedding_function=ef
        )

    def add(
        self,
        content: str,
        memory_type: str,
        user_id: str,
        source: str = "agent",
    ) -> str:
        """Store a memory entry. Returns the generated memory_id.

        Raises:
            Exception: propagates ChromaDB errors so callers can handle them.
        """
        memory_id = str(uuid.uuid4())
        collection = self._get_collection()
        collection.add(
            documents=[content],
            ids=[memory_id],
            metadatas=[
                {
                    "user_id": user_id,
                    "type": memory_type,
                    "source": source,
                    "timestamp": time.time(),
                }
            ],
        )
        return memory_id

    def search(
        self,
        query: str,
        user_id: str,
        n_results: int = 5,
    ) -> List[Dict]:
        """Semantic search over agent_memory for a specific user.

        Returns list of dicts with 'content' + all metadata fields merged.

        Raises:
            Exception: propagates ChromaDB errors so callers can handle them.
        """
        collection = self._get_collection()
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            where={"user_id": user_id},
        )
        entries = []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        for doc, meta in zip(documents, metadatas):
            entry = {"content": doc}
            entry.update(meta)
            entries.append(entry)
        return entries

    def get_recent(self, user_id: str, n: int = 8) -> List[Dict]:
        """Return the n most recent memories for a user.

        Fetches up to 50 entries from ChromaDB, sorts by timestamp descending,
        returns top n.

        Raises:
            Exception: propagates ChromaDB errors so callers can handle them.
        """
        collection = self._get_collection()
        results = collection.get(
            where={"user_id": user_id},
            limit=50,
            include=["documents", "metadatas"],
        )
        entries = []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        for doc, meta in zip(documents, metadatas):
            entry = {"content": doc}
            entry.update(meta)
            entries.append(entry)

        # Sort by timestamp descending (newest first)
        entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return entries[:n]
