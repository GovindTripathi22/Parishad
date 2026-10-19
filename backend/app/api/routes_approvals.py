"""
CortexOS — Human-in-the-Loop Approvals (View 2)
List, inspect, approve, and decline pending drafts.
Approving a draft atomically updates the official document, bumps its version,
updates extracted rules, and records an immutable SHA-256 ledger entry.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_db
from app.database.models import (
    DecisionLedgerEntry,
    DocRule,
    OfficialDoc,
    PendingApproval,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ApprovalItem(BaseModel):
    id: str  # draft_id (e.g. 'REV-101')
    draft_id: str
    parameter: str
    parameter_label: str
    official_value: str
    proposed_value: str
    status: str
    source: str
    category: str
    topic: str
    rule_id: str
    target_doc_id: str
    confidence: float
    explanation: str
    reason: str
    old_text: str
    proposed_text: str
    created_at: Optional[str] = None
    council_result: Optional[str] = None
    note: Optional[str] = None


class ApproveRequest(BaseModel):
    note: Optional[str] = ""
    proposed_value: Optional[str] = None
    edited_text: Optional[str] = None


class DeclineRequest(BaseModel):
    note: Optional[str] = ""


class ActionResponse(BaseModel):
    id: str
    status: str
    message: str
    doc_id: Optional[str] = None
    ledger_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_approval(row: PendingApproval) -> dict[str, Any]:
    c_note = ""
    if row.council_opinion and isinstance(row.council_opinion, dict):
        c_note = row.council_opinion.get("review_notes", "")

    return {
        "id": row.draft_id,
        "draft_id": row.draft_id,
        "parameter": row.parameter_label,
        "parameter_label": row.parameter_label,
        "official_value": row.old_value,
        "proposed_value": row.new_value,
        "status": row.status.lower(),
        "source": row.source,
        "category": row.topic,
        "topic": row.topic,
        "rule_id": row.target_doc_id,
        "target_doc_id": row.target_doc_id,
        "confidence": row.council_confidence or 0.95,
        "explanation": row.reason,
        "reason": row.reason,
        "old_text": row.old_text,
        "proposed_text": row.proposed_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "council_result": c_note or "Council of Agents: Verified groundedness and staged for human sign-off.",
        "note": row.review_note or "",
    }


def _sha256_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="List all pending and historical approvals",
)
async def list_approvals(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Returns approval drafts, optionally filtered by status."""
    stmt = select(PendingApproval).order_by(PendingApproval.created_at.desc())
    if status_filter:
        stmt = stmt.where(PendingApproval.status == status_filter.upper())
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [_format_approval(r) for r in rows]


@router.get(
    "/{draft_id}",
    summary="Get single approval draft details",
)
async def get_approval(draft_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch single approval by draft_id (e.g. 'REV-101') or database ID."""
    stmt = select(PendingApproval).where(
        (PendingApproval.draft_id == draft_id) | (PendingApproval.id == draft_id)
    )
    result = await db.execute(stmt)
    approval = result.scalars().first()
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Approval draft '{draft_id}' not found")
    return _format_approval(approval)


@router.post(
    "/{draft_id}/approve",
    response_model=ActionResponse,
    summary="Approve a draft and update the official document",
)
async def approve_draft(
    draft_id: str,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Human sign-off action:
      1. Commits proposed changes to the targeted official document
      2. Bumps document version (e.g. v2.4 -> v2.5)
      3. Synchronizes extracted rules
      4. Creates a permanent SHA-256 hashed ledger record
      5. Marks draft as APPROVED
    """
    stmt = select(PendingApproval).where(
        (PendingApproval.draft_id == draft_id) | (PendingApproval.id == draft_id)
    )
    result = await db.execute(stmt)
    approval = result.scalars().first()
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval draft not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Draft already resolved as {approval.status}")

    now = datetime.now(timezone.utc)
    approval_note = body.note or "Approved via CortexOS Human-in-the-Loop Gate."
    updated_text = body.edited_text or approval.proposed_text

    # If user edited proposed_value in UI textarea, update it
    final_new_val = body.proposed_value or approval.new_value

    # 1. Update linked official document
    doc_stmt = select(OfficialDoc).options(selectinload(OfficialDoc.rules)).where(OfficialDoc.doc_id == approval.target_doc_id)
    doc_res = await db.execute(doc_stmt)
    doc = doc_res.scalars().first()

    if doc:
        doc.content = updated_text
        doc.updated_at = now
        try:
            cur_ver = float(doc.version.replace("v", ""))
            doc.version = f"v{cur_ver + 0.1:.1f}"
        except Exception:
            doc.version = f"{doc.version}.1"

        # Update matching rule value if numeric
        if approval.numeric_new is not None:
            for rule in doc.rules:
                if rule.unit.lower() == approval.unit.lower():
                    rule.value = approval.numeric_new

    # 2. Add immutable Decision Ledger entry
    total_ledger = (await db.execute(select(func.count(DecisionLedgerEntry.id)))).scalar() or 0
    decision_id = f"DEC-2026-{total_ledger + 1:02d}"

    ledger_payload = {
        "decision_id": decision_id,
        "draft_id": approval.draft_id,
        "doc_id": approval.target_doc_id,
        "parameter": approval.parameter_label,
        "old_value": approval.old_value,
        "new_value": final_new_val,
        "status": "APPROVED",
        "note": approval_note,
        "timestamp": now.isoformat(),
    }
    hash_str = _sha256_hash(ledger_payload)

    ledger_entry = DecisionLedgerEntry(
        decision_id=decision_id,
        timestamp=now,
        topic=approval.topic,
        parameter=approval.parameter_label,
        old_value=approval.old_value,
        new_value=final_new_val,
        status="APPROVED",
        doc_id=approval.target_doc_id,
        note=approval_note,
        hash=hash_str,
        source_draft_id=approval.draft_id,
        reviewed_by="Human Reviewer",
    )
    db.add(ledger_entry)

    # 3. Update approval record
    approval.status = "APPROVED"
    approval.reviewed_at = now
    approval.review_note = approval_note
    approval.proposed_text = updated_text
    approval.new_value = final_new_val

    await db.commit()

    return ActionResponse(
        id=approval.draft_id,
        status="APPROVED",
        message=f"Draft {approval.draft_id} approved. Document {approval.target_doc_id} updated to {doc.version if doc else 'latest'}.",
        doc_id=approval.target_doc_id,
        ledger_id=decision_id,
    )


@router.post(
    "/{draft_id}/decline",
    response_model=ActionResponse,
    summary="Decline a draft and log in decision history",
)
async def decline_draft(
    draft_id: str,
    body: DeclineRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Decline proposed change: preserves existing official specifications
    and logs the decline into the audit ledger.
    """
    stmt = select(PendingApproval).where(
        (PendingApproval.draft_id == draft_id) | (PendingApproval.id == draft_id)
    )
    result = await db.execute(stmt)
    approval = result.scalars().first()
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval draft not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Draft already resolved as {approval.status}")

    now = datetime.now(timezone.utc)
    decline_note = body.note or "Declined to maintain certified parameters and specifications."

    total_ledger = (await db.execute(select(func.count(DecisionLedgerEntry.id)))).scalar() or 0
    decision_id = f"DEC-2026-{total_ledger + 1:02d}"

    ledger_payload = {
        "decision_id": decision_id,
        "draft_id": approval.draft_id,
        "doc_id": approval.target_doc_id,
        "parameter": approval.parameter_label,
        "old_value": approval.old_value,
        "new_value": f"{approval.new_value} (Declined)",
        "status": "DECLINED",
        "note": decline_note,
        "timestamp": now.isoformat(),
    }

    ledger_entry = DecisionLedgerEntry(
        decision_id=decision_id,
        timestamp=now,
        topic=approval.topic,
        parameter=approval.parameter_label,
        old_value=approval.old_value,
        new_value=f"{approval.new_value} (Declined)",
        status="DECLINED",
        doc_id=approval.target_doc_id,
        note=decline_note,
        hash=_sha256_hash(ledger_payload),
        source_draft_id=approval.draft_id,
        reviewed_by="Human Reviewer",
    )
    db.add(ledger_entry)

    approval.status = "DECLINED"
    approval.reviewed_at = now
    approval.review_note = decline_note

    await db.commit()

    return ActionResponse(
        id=approval.draft_id,
        status="DECLINED",
        message=f"Draft {approval.draft_id} declined. Official doc {approval.target_doc_id} remained unchanged.",
        doc_id=approval.target_doc_id,
        ledger_id=decision_id,
    )
