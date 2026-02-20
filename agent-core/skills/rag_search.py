"""
RAG search skill — queries the local ChromaDB vector store.

Replaces the hardcoded "search docs" keyword shortcut in app.py with a
proper skill that the LLM can call when it determines a local knowledge
base lookup would be helpful.
"""

from typing import Any, Dict, List, Tuple

from skills.base import SkillBase, SkillMetadata
from policy import RiskLevel


class RagSearchSkill(SkillBase):
    """Search the local ChromaDB knowledge base for relevant documents."""

    CHROMA_HOST = "chroma-rag"
    CHROMA_PORT = 8000
    COLLECTION_NAME = "rag_data"
    MAX_OUTPUT_CHARS = 2000
    N_RESULTS = 3

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="rag_search",
            description=(
                "Search the local knowledge base (ChromaDB) for documents "
                "relevant to a query. Use this when you need to look up "
                "information from uploaded or indexed documents."
            ),
            risk_level=RiskLevel.LOW,
            rate_limit="rag_search",
            requires_approval=False,
            max_calls_per_turn=5,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant documents.",
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
        if len(query) > 1000:
            return False, "Parameter 'query' must be under 1000 characters"
        return True, ""

    async def execute(self, params: Dict[str, Any]) -> List[str]:
        """Query ChromaDB and return matching document strings."""
        import chromadb

        query = params["query"]
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            ef = DefaultEmbeddingFunction()
            chroma_client = chromadb.HttpClient(
                host=self.CHROMA_HOST, port=self.CHROMA_PORT
            )
            collection = chroma_client.get_or_create_collection(
                self.COLLECTION_NAME, embedding_function=ef
            )
            results = collection.query(query_texts=[query], n_results=self.N_RESULTS)
            return results["documents"][0]
        except Exception:
            return []

    def sanitize_output(self, result: Any) -> str:
        """Join documents and truncate to MAX_OUTPUT_CHARS."""
        if not result:
            return "No relevant documents found."
        joined = "\n\n".join(str(doc) for doc in result)
        if len(joined) > self.MAX_OUTPUT_CHARS:
            return joined[: self.MAX_OUTPUT_CHARS] + "\n[truncated]"
        return joined
