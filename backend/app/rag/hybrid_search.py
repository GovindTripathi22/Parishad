"""
CortexOS — Hybrid Search Module.

Combines BM25 lexical search, TF-IDF n-gram scoring, and vector semantic similarity
into a unified ranking algorithm with domain and unit-preference boosts.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field

from app.rag.embeddings import _local_fallback_embedding, generate_embedding


# ── Search Models ─────────────────────────────────────────────────────────────

class DocumentHit(BaseModel):
    """A scored document result."""
    doc_id: str = Field(..., description="Canonical Document ID (e.g. 'DOC-BMS-402').")
    id: str = Field("", description="Compatibility alias for doc_id.")
    title: str = Field(..., description="Document title.")
    topic: str = Field(..., description="Domain/topic classification.")
    content: str = Field(..., description="Document text content.")
    snippet: str = Field("", description="Relevant text excerpt.")
    score: float = Field(..., description="Combined normalized hybrid score (0.0–1.0).")
    bm25_score: float = Field(0.0, description="Raw or normalized BM25 score.")
    tfidf_score: float = Field(0.0, description="TF-IDF term overlap score.")
    vector_score: float = Field(0.0, description="Cosine vector similarity score.")
    rules: list[dict[str, Any]] = Field(default_factory=list, description="Associated document rules.")

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = self.doc_id
        if not self.snippet:
            self.snippet = self.content[:240] + ("…" if len(self.content) > 240 else "")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class SearchResult(BaseModel):
    """Container returned by hybrid_search."""
    query: str = ""
    results: list[dict[str, Any]] = Field(default_factory=list)
    total_hits: int = 0

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.results[index]


# ── Built-in Canonical Documents ──────────────────────────────────────────────

DEFAULT_DOCUMENTS: list[dict[str, Any]] = [
    {
        "doc_id": "DOC-BMS-402",
        "topic": "BMS-48V",
        "title": "48V Smart Battery Sensor Electrical & Thermal Specification",
        "version": "v2.4",
        "content": "Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125C.",
        "rules": [
            {"label": "Max Voltage Limit", "value": 54.0, "unit": "v"},
            {"label": "Thermal Shutdown Limit", "value": 125.0, "unit": "c"},
        ],
    },
    {
        "doc_id": "DOC-RAD-118",
        "topic": "RADAR-77GHZ",
        "title": "77GHz Corner Radar CAN-FD Communication Specification",
        "version": "v3.1",
        "content": "Primary bus uses CAN-FD. Approved data phase speed is 2.0 Mbps on standard twisted-pair harnesses to guarantee electromagnetic safety.",
        "rules": [
            {"label": "CAN-FD Data Speed", "value": 2.0, "unit": "mbps"},
        ],
    },
    {
        "doc_id": "DOC-POLICY-101",
        "topic": "COMPANY-OPS",
        "title": "Customer Refund & Support SLA Policy",
        "version": "v1.8",
        "content": "Customers are guaranteed a full self-serve refund within 30 days of purchase. Critical support tickets must receive a response within 4 hours.",
        "rules": [
            {"label": "Refund Window", "value": 30, "unit": "days"},
            {"label": "Support SLA Response", "value": 4, "unit": "hours"},
        ],
    },
    {
        "doc_id": "DOC-CLOUD-305",
        "topic": "CLOUD-INFRA",
        "title": "Cloud Infrastructure Latency & Budget Policy",
        "version": "v1.2",
        "content": "Production API gateway response latency must stay under 200 ms. Maximum approved monthly cluster budget is $5000.",
        "rules": [
            {"label": "Max API Latency", "value": 200, "unit": "ms"},
            {"label": "Monthly Cloud Budget", "value": 5000, "unit": "usd"},
        ],
    },
    {
        "doc_id": "DOC-ARCH-200",
        "topic": "ARCHITECTURE",
        "title": "Service Architecture Decision Record",
        "version": "v1.0",
        "content": "All new services must be containerized with Docker. Maximum memory allocation per pod is 2 GB. Inter-service communication uses gRPC with 50 ms timeout.",
        "rules": [
            {"label": "Max Pod Memory", "value": 2, "unit": "gb"},
            {"label": "gRPC Timeout", "value": 50, "unit": "ms"},
        ],
    },
]


# ── Text Preprocessing & BM25 / TF-IDF Implementation ─────────────────────────

STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by",
    "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "our",
    "ours", "ourselves", "out", "over", "own", "s", "same", "she", "should", "so", "some", "such",
    "t", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "you",
    "your", "yours", "yourself", "yourselves",
}


def _tokenize(text: str) -> list[str]:
    """Tokenize and remove common stop words."""
    tokens = re.findall(r"[a-zA-Z0-9_-]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]


def _cosine(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Compute cosine similarity between two numeric vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 < 1e-9 or norm2 < 1e-9:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))


class BM25Engine:
    """In-memory BM25 ranker."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(1, self.corpus_size)
        self.df: dict[str, int] = {}
        for doc in corpus:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        doc = self.corpus[doc_idx]
        doc_len = len(doc)
        doc_tf: dict[str, int] = {}
        for term in doc:
            doc_tf[term] = doc_tf.get(term, 0) + 1

        score = 0.0
        for term in query_terms:
            if term not in doc_tf:
                continue
            df = self.df.get(term, 0)
            idf = math.log(1.0 + (self.corpus_size - df + 0.5) / (df + 0.5))
            tf = doc_tf[term]
            num = tf * (self.k1 + 1.0)
            den = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / max(1.0, self.avgdl)))
            score += idf * (num / den)
        return score


# ── Main Hybrid Search Function ───────────────────────────────────────────────

def hybrid_search(
    query: str,
    documents: list[dict[str, Any]] | None = None,
    top_k: int = 5,
    prefer_topic: str | None = None,
    prefer_unit: str | None = None,
) -> SearchResult:
    """
    Search documents using BM25 + TF-IDF keyword match + vector embedding similarity.
    Can be called synchronously or asynchronously.
    """
    docs = documents if documents is not None and len(documents) > 0 else DEFAULT_DOCUMENTS
    if not query.strip():
        # Return default top_k
        hits = [
            DocumentHit(
                doc_id=d.get("doc_id", "DOC-000"),
                title=d.get("title", "Untitled"),
                topic=d.get("topic", "GENERAL"),
                content=d.get("content", ""),
                score=0.5,
                rules=d.get("rules", []),
            ).model_dump()
            for d in docs[:top_k]
        ]
        return SearchResult(query=query, results=hits, total_hits=len(hits))

    query_tokens = _tokenize(query)
    q_vec = _local_fallback_embedding(query)

    # Prepare document text corpus
    corpus_tokens: list[list[str]] = []
    doc_vecs: list[list[float]] = []

    for d in docs:
        doc_text = f"{d.get('title', '')} {d.get('topic', '')} {d.get('content', '')}"
        corpus_tokens.append(_tokenize(doc_text))
        doc_vecs.append(_local_fallback_embedding(doc_text))

    bm25 = BM25Engine(corpus_tokens)

    # Compute raw scores
    scored_items: list[tuple[float, dict[str, Any], float, float, float]] = []
    max_bm25 = 1e-6

    raw_bm25_scores = [bm25.score(query_tokens, i) for i in range(len(docs))]
    max_bm25 = max(max_bm25, max(raw_bm25_scores) if raw_bm25_scores else 1.0)

    for i, d in enumerate(docs):
        # 1. BM25 normalized (0.0 - 1.0)
        norm_bm25 = raw_bm25_scores[i] / max_bm25

        # 2. Simple TF-IDF overlap
        dtoks = set(corpus_tokens[i])
        overlap_count = sum(1 for q in query_tokens if q in dtoks)
        tfidf_score = min(1.0, overlap_count / max(1, len(query_tokens)))

        # 3. Vector similarity
        v_score = _cosine(q_vec, doc_vecs[i])

        # Base hybrid combination (BM25: 35%, TF-IDF: 25%, Vector: 40%)
        combined = 0.35 * norm_bm25 + 0.25 * tfidf_score + 0.40 * v_score

        # Domain/topic preference boost
        d_topic = str(d.get("topic", "")).upper()
        if prefer_topic and prefer_topic.upper() != "AUTO" and prefer_topic.upper() in d_topic:
            combined += 0.25

        # Unit preference boost (e.g. if looking for voltage and doc has 'v')
        d_rules = d.get("rules", [])
        if prefer_unit and any(str(r.get("unit", "")).lower() == prefer_unit.lower() for r in d_rules):
            combined += 0.25

        # Clamp combined score between 0.0 and 0.99
        final_score = round(min(0.99, max(0.01, combined)), 4)
        scored_items.append((final_score, d, round(norm_bm25, 4), round(tfidf_score, 4), round(v_score, 4)))

    scored_items.sort(key=lambda x: x[0], reverse=True)

    results: list[dict[str, Any]] = []
    for final_score, d, b_sc, tf_sc, v_sc in scored_items[:top_k]:
        hit = DocumentHit(
            doc_id=d.get("doc_id", "DOC-000"),
            title=d.get("title", "Untitled"),
            topic=d.get("topic", "GENERAL"),
            content=d.get("content", ""),
            snippet=d.get("content", "")[:240],
            score=final_score,
            bm25_score=b_sc,
            tfidf_score=tf_sc,
            vector_score=v_sc,
            rules=d.get("rules", []),
        )
        results.append(hit.model_dump())

    return SearchResult(query=query, results=results, total_hits=len(results))
