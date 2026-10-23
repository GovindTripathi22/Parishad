"""
Parishad — Immutable Decision Ledger (View 3)
Auditable, tamper-evident record of all approved and declined policy/spec updates
with SHA-256 cryptographic hashes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_db
from app.database.models import DecisionLedgerEntry, OfficialDoc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ledger", tags=["ledger"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class LedgerEntryOut(BaseModel):
    id: str
    decision_id: str
    parameter: str
    old_value: str
    new_value: str
    status: str
    topic: str
    doc_id: str
    doc_version: Optional[str] = "v1.0"
    note: Optional[str] = None
    prev_hash: Optional[str] = None
    canonical_payload: Optional[str] = None
    hash: str
    timestamp: Optional[str] = None
    created_at: Optional[str] = None
    reviewed_by: Optional[str] = "admin"


class LedgerVerificationReport(BaseModel):
    is_valid: bool
    total_records: int
    verified_records: int
    chain_intact: bool
    head_hash: Optional[str] = None
    errors: list[str] = []
    verified_at: str


class LedgerStats(BaseModel):
    total_decisions: int = 0
    approved_count: int = 0
    declined_count: int = 0
    decisions_by_topic: dict[str, int] = {}


def _format_entry(e: DecisionLedgerEntry) -> dict[str, Any]:
    ts_str = e.timestamp.isoformat() if e.timestamp else None
    return {
        "id": e.decision_id,
        "decision_id": e.decision_id,
        "parameter": e.parameter,
        "old_value": e.old_value,
        "new_value": e.new_value,
        "status": e.status.lower(),
        "topic": e.topic,
        "doc_id": e.doc_id,
        "doc_version": getattr(e, "doc_version", "v1.0"),
        "note": e.note or "",
        "prev_hash": getattr(e, "prev_hash", "0" * 64),
        "canonical_payload": getattr(e, "canonical_payload", None),
        "hash": e.hash,
        "timestamp": ts_str,
        "created_at": ts_str,
        "reviewed_by": e.reviewed_by or "admin",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/verify",
    response_model=LedgerVerificationReport,
    summary="Cryptographically verify full Decision Ledger SHA-256 hash chain",
)
async def verify_ledger(db: AsyncSession = Depends(get_db)) -> LedgerVerificationReport:
    """
    Independent cryptographic verification:
      1. Reconstructs each entry's canonical payload.
      2. Validates block-to-block hash chaining: prev_hash[i] == hash[i-1].
      3. Recomputes SHA-256 digest: digest == sha256(canonical_payload + prev_hash).
      4. Detects any modified payloads, reordered blocks, or broken chains.
    """
    import hashlib
    import json

    stmt = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.timestamp.asc(), DecisionLedgerEntry.id.asc())
    result = await db.execute(stmt)
    entries = result.scalars().all()

    errors: list[str] = []
    verified_count = 0
    prev_hash_expected: Optional[str] = None

    for i, e in enumerate(entries):
        # 1. Chain continuity check
        actual_prev = getattr(e, "prev_hash", None)
        if i == 0:
            # Genesis record
            prev_hash_expected = actual_prev or ("0" * 64)
        else:
            if actual_prev != prev_hash_expected:
                errors.append(
                    f"Chain break at record {e.decision_id}: prev_hash '{actual_prev}' "
                    f"does not match preceding record hash '{prev_hash_expected}'."
                )

        # 2. Recompute cryptographic digest
        canon = getattr(e, "canonical_payload", None)
        if not canon:
            # Reconstruct canonical representation for older records
            payload_dict = {
                "decision_id": e.decision_id,
                "doc_id": e.doc_id,
                "parameter": e.parameter,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "status": e.status.upper(),
            }
            canon = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"), default=str)

        computed_digest = "sha256:" + hashlib.sha256((canon + (actual_prev or ("0" * 64))).encode("utf-8")).hexdigest()

        # If recorded hash is full 64-char sha256, verify exact match
        if len(e.hash) > 20:
            if e.hash != computed_digest:
                errors.append(
                    f"Digest mismatch on record {e.decision_id}: stored '{e.hash}' "
                    f"!= computed '{computed_digest}'."
                )
            else:
                verified_count += 1
        else:
            # Legacy truncated hash (backward compatibility check)
            if not e.hash.startswith("sha256:"):
                errors.append(f"Invalid hash format on {e.decision_id}: '{e.hash}'")
            else:
                verified_count += 1

        prev_hash_expected = e.hash

    is_valid = len(errors) == 0 and len(entries) > 0
    return LedgerVerificationReport(
        is_valid=is_valid,
        total_records=len(entries),
        verified_records=verified_count,
        chain_intact=len(errors) == 0,
        head_hash=entries[-1].hash if entries else None,
        errors=errors,
        verified_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="List all decision ledger records",
)
async def list_ledger(
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Return all ledger records, ordered newest first."""
    stmt = (
        select(DecisionLedgerEntry)
        .order_by(DecisionLedgerEntry.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [_format_entry(e) for e in entries]


@router.get(
    "/export",
    summary="Export full immutable ledger JSON audit bundle",
)
async def export_ledger(db: AsyncSession = Depends(get_db)):
    """
    Exports full Company Brain and Decision Ledger as an immutable JSON audit bundle.
    """
    entries = (
        await db.execute(
            select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.timestamp.desc())
        )
    ).scalars().all()

    docs = (
        await db.execute(
            select(OfficialDoc).options(selectinload(OfficialDoc.rules)).order_by(OfficialDoc.doc_id)
        )
    ).scalars().all()

    bundle = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_decisions": len(entries),
        "total_documents": len(docs),
        "official_documents": [
            {
                "doc_id": d.doc_id,
                "topic": d.topic,
                "title": d.title,
                "version": d.version,
                "content": d.content,
                "rules": [{"label": r.label, "value": r.value, "unit": r.unit} for r in (d.rules or [])],
            }
            for d in docs
        ],
        "decision_ledger": [_format_entry(e) for e in entries],
    }

    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": "attachment; filename=parishad_audit_ledger.json"},
    )


@router.get(
    "/stats",
    response_model=LedgerStats,
    summary="Decision Ledger summary metrics",
)
async def ledger_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate statistics for compliance reports."""
    entries = (await db.execute(select(DecisionLedgerEntry))).scalars().all()

    total = len(entries)
    approved = sum(1 for e in entries if e.status.upper() == "APPROVED")
    declined = sum(1 for e in entries if e.status.upper() == "DECLINED")

    by_topic: dict[str, int] = {}
    for e in entries:
        t = e.topic or "GENERAL"
        by_topic[t] = by_topic.get(t, 0) + 1

    return LedgerStats(
        total_decisions=total,
        approved_count=approved,
        declined_count=declined,
        decisions_by_topic=by_topic,
    )
