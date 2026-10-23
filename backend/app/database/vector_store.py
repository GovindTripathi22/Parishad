"""
Parishad Vector Store — In-memory vector storage with persistence via SQLite.

Provides cosine-similarity search over document chunk embeddings.
Used as a component of the hybrid search pipeline.
"""
from __future__ import annotations

import json
import math
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import VectorChunk

logger = logging.getLogger("parishad.vector_store")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorStore:
    """
    In-memory vector store backed by SQLite for persistence.

    On startup, loads all vectors from the `vector_chunks` table.
    Supports add, search, and delete operations.
    """

    def __init__(self):
        self._vectors: dict[str, dict] = {}  # chunk_id -> {doc_id, text, embedding}
        self._loaded = False

    async def load_from_db(self, session: AsyncSession) -> int:
        """Load all vector chunks from the database into memory."""
        result = await session.execute(select(VectorChunk))
        chunks = result.scalars().all()
        count = 0
        for chunk in chunks:
            if chunk.embedding:
                try:
                    embedding = json.loads(chunk.embedding)
                    self._vectors[chunk.id] = {
                        "doc_id": chunk.doc_id,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                        "embedding": embedding,
                    }
                    count += 1
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Skipping chunk {chunk.id}: invalid embedding JSON")
        self._loaded = True
        logger.info(f"Loaded {count} vector chunks into memory.")
        return count

    async def add_chunk(
        self,
        session: AsyncSession,
        doc_id: str,
        text: str,
        embedding: list[float],
        chunk_index: int = 0,
    ) -> str:
        """Add a new vector chunk to memory and persist to DB."""
        chunk = VectorChunk(
            doc_id=doc_id,
            chunk_index=chunk_index,
            text=text,
            embedding=json.dumps(embedding),
        )
        session.add(chunk)
        await session.flush()

        self._vectors[chunk.id] = {
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "text": text,
            "embedding": embedding,
        }
        return chunk.id

    async def remove_by_doc(self, session: AsyncSession, doc_id: str) -> int:
        """Remove all chunks for a given doc_id."""
        result = await session.execute(
            select(VectorChunk).where(VectorChunk.doc_id == doc_id)
        )
        chunks = result.scalars().all()
        removed = 0
        for chunk in chunks:
            await session.delete(chunk)
            self._vectors.pop(chunk.id, None)
            removed += 1
        return removed

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.0,
        filter_doc_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Find the top-k most similar chunks by cosine similarity.

        Returns list of dicts: {chunk_id, doc_id, text, score, chunk_index}
        """
        results = []
        for chunk_id, data in self._vectors.items():
            if filter_doc_id and data["doc_id"] != filter_doc_id:
                continue
            score = cosine_similarity(query_embedding, data["embedding"])
            if score >= threshold:
                results.append({
                    "chunk_id": chunk_id,
                    "doc_id": data["doc_id"],
                    "chunk_index": data["chunk_index"],
                    "text": data["text"],
                    "score": round(score, 4),
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    @property
    def count(self) -> int:
        return len(self._vectors)

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# Singleton instance
vector_store = VectorStore()
