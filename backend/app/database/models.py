"""
CortexOS Database Models — SQLAlchemy ORM for all persistent entities.

Tables:
  - official_docs:      Source-of-truth company documents & specifications
  - doc_rules:          Extracted numeric rules from official docs
  - pending_approvals:  Drafts waiting for human sign-off
  - decision_ledger:    Immutable audit trail of every approved/declined change
  - ingestion_events:   Raw events captured from GitHub/Slack/Jira/GDrive
  - brain_queries:      Log of every "Ask the Brain" query + answer
  - vector_chunks:      Document chunks with serialized embedding vectors
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Float, Integer, Boolean,
    DateTime, ForeignKey, JSON, Enum as SAEnum, Index,
)
from sqlalchemy.orm import relationship, DeclarativeBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════
#  OFFICIAL DOCUMENTS — Source of Truth
# ═══════════════════════════════════════════════════════════════
class OfficialDoc(Base):
    __tablename__ = "official_docs"

    id = Column(String(32), primary_key=True, default=_new_id)
    doc_id = Column(String(32), unique=True, nullable=False, index=True)
    topic = Column(String(64), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    version = Column(String(16), nullable=False, default="v1.0")
    content = Column(Text, nullable=False)
    source = Column(String(64), default="manual")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    is_active = Column(Boolean, default=True)

    rules = relationship("DocRule", back_populates="doc", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<OfficialDoc {self.doc_id} v{self.version}>"


class DocRule(Base):
    __tablename__ = "doc_rules"

    id = Column(String(32), primary_key=True, default=_new_id)
    doc_id = Column(String(32), ForeignKey("official_docs.doc_id"), nullable=False)
    label = Column(String(128), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=False)

    doc = relationship("OfficialDoc", back_populates="rules")

    __table_args__ = (
        Index("ix_doc_rules_unit", "unit"),
    )


# ═══════════════════════════════════════════════════════════════
#  PENDING APPROVALS — Human-in-the-Loop Gate
# ═══════════════════════════════════════════════════════════════
class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id = Column(String(32), primary_key=True, default=_new_id)
    draft_id = Column(String(32), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    source = Column(String(128), nullable=False)
    topic = Column(String(64), nullable=False)
    target_doc_id = Column(String(32), ForeignKey("official_docs.doc_id"), nullable=False)
    parameter_label = Column(String(128), nullable=False)
    unit = Column(String(32), nullable=False)
    old_value = Column(String(64), nullable=False)
    new_value = Column(String(64), nullable=False)
    numeric_new = Column(Float, nullable=True)
    reason = Column(Text, nullable=False)
    old_text = Column(Text, nullable=False)
    proposed_text = Column(Text, nullable=False)
    target_doc_version = Column(String(32), default="v1.0", nullable=False)
    status = Column(String(16), default="PENDING", index=True)  # PENDING | APPROVED | DECLINED
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)

    # Council of Agents verification
    council_opinion = Column(JSON, nullable=True)
    council_confidence = Column(Float, nullable=True)


# ═══════════════════════════════════════════════════════════════
#  DECISION LEDGER — Immutable Audit Trail
# ═══════════════════════════════════════════════════════════════
class DecisionLedgerEntry(Base):
    __tablename__ = "decision_ledger"

    id = Column(String(32), primary_key=True, default=_new_id)
    decision_id = Column(String(32), unique=True, nullable=False, index=True)
    timestamp = Column(DateTime, default=_utcnow)
    topic = Column(String(64), nullable=False)
    parameter = Column(String(128), nullable=False)
    old_value = Column(String(128), nullable=False)
    new_value = Column(String(128), nullable=False)
    status = Column(String(16), nullable=False)  # APPROVED | DECLINED
    doc_id = Column(String(32), nullable=False)
    doc_version = Column(String(32), default="v1.0", nullable=False)
    note = Column(Text, nullable=True)
    prev_hash = Column(String(128), default="0" * 64, nullable=False)
    canonical_payload = Column(Text, nullable=True)
    hash = Column(String(128), nullable=False)
    source_draft_id = Column(String(32), nullable=True)
    reviewed_by = Column(String(128), default="admin")


# ═══════════════════════════════════════════════════════════════
#  INGESTION EVENTS — Raw captures from external sources
# ═══════════════════════════════════════════════════════════════
class IngestionEvent(Base):
    __tablename__ = "ingestion_events"

    id = Column(String(32), primary_key=True, default=_new_id)
    source_type = Column(String(32), nullable=False, index=True)  # github | slack | jira | gdrive
    source_id = Column(String(256), nullable=True)  # PR number, channel ID, ticket key, file ID
    raw_content = Column(Text, nullable=False)
    clean_content = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSON, nullable=True)
    is_signal = Column(Boolean, default=False)  # True if passed signal filter
    signal_type = Column(String(64), nullable=True)  # decision | sop | architecture | policy
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_ingestion_source", "source_type", "source_id"),
    )


# ═══════════════════════════════════════════════════════════════
#  BRAIN QUERIES — Audit log of "Ask the Company Brain" queries
# ═══════════════════════════════════════════════════════════════
class BrainQuery(Base):
    __tablename__ = "brain_queries"

    id = Column(String(32), primary_key=True, default=_new_id)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    matched_doc_ids = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


# ═══════════════════════════════════════════════════════════════
#  VECTOR CHUNKS — Embeddings for hybrid semantic search
# ═══════════════════════════════════════════════════════════════
class VectorChunk(Base):
    __tablename__ = "vector_chunks"

    id = Column(String(32), primary_key=True, default=_new_id)
    doc_id = Column(String(32), nullable=False, index=True)
    chunk_index = Column(Integer, default=0)
    text = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)  # JSON-serialized float list
    created_at = Column(DateTime, default=_utcnow)
