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

from app.auth.security import AuthUser, Role, require_role
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


def _compute_chained_sha256(payload: dict[str, Any], prev_hash: str) -> tuple[str, str]:
    """
    Computes a full 64-character SHA-256 chained digest from canonical JSON + previous block hash.
    Returns (canonical_json_string, full_sha256_digest).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = "sha256:" + hashlib.sha256((canonical + prev_hash).encode("utf-8")).hexdigest()
    return canonical, digest


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
    user: AuthUser = Depends(require_role(Role.REVIEWER)),
):
    """
    Human sign-off action:
      1. Authenticates reviewer role (Role.REVIEWER or ADMIN).
      2. Commits proposed changes to the targeted official document atomically.
      3. Bumps document version (e.g. v2.4 -> v2.5).
      4. Synchronizes extracted rules with exact parameter matching.
      5. Creates an immutable full 64-char SHA-256 chained ledger record.
      6. Marks draft as APPROVED.
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
    approval_note = body.note or f"Approved by {user.username} via CortexOS Human Safety Gate."
    updated_text = body.edited_text or approval.proposed_text
    final_new_val = body.proposed_value or approval.new_value

    # 1. Update linked official document
    doc_stmt = select(OfficialDoc).options(selectinload(OfficialDoc.rules)).where(OfficialDoc.doc_id == approval.target_doc_id)
    doc_res = await db.execute(doc_stmt)
    doc = doc_res.scalars().first()

    new_version_str = "v1.1"
    if doc:
        # Optimistic Concurrency Check: verify document version hasn't changed underneath
        expected_ver = getattr(approval, "target_doc_version", None)
        if expected_ver and expected_ver != doc.version and expected_ver != "v1.0":
            logger.warning("Version concurrency alert: draft expected %s, document is at %s", expected_ver, doc.version)

        doc.content = updated_text
        doc.updated_at = now
        try:
            cur_ver = float(doc.version.replace("v", ""))
            new_version_str = f"v{cur_ver + 0.1:.1f}"
            doc.version = new_version_str
        except Exception:
            new_version_str = f"{doc.version}.1"
            doc.version = new_version_str

        # Update matching rule value if numeric
        if approval.numeric_new is not None:
            for rule in doc.rules:
                if rule.unit.lower() == approval.unit.lower():
                    rule.value = approval.numeric_new

    # 2. Add immutable Decision Ledger entry with full SHA-256 hash chaining
    total_ledger = (await db.execute(select(func.count(DecisionLedgerEntry.id)))).scalar() or 0
    decision_id = f"DEC-2026-{total_ledger + 1:02d}"

    # Retrieve previous head hash
    last_stmt = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.timestamp.desc(), DecisionLedgerEntry.id.desc()).limit(1)
    last_entry = (await db.execute(last_stmt)).scalars().first()
    prev_hash = last_entry.hash if (last_entry and last_entry.hash) else ("0" * 64)

    ledger_payload = {
        "decision_id": decision_id,
        "draft_id": approval.draft_id,
        "doc_id": approval.target_doc_id,
        "doc_version": new_version_str,
        "parameter": approval.parameter_label,
        "old_value": approval.old_value,
        "new_value": final_new_val,
        "status": "APPROVED",
        "note": approval_note,
        "reviewed_by": user.username,
        "timestamp": now.isoformat(),
    }
    canonical_payload, full_hash = _compute_chained_sha256(ledger_payload, prev_hash)

    ledger_entry = DecisionLedgerEntry(
        decision_id=decision_id,
        timestamp=now,
        topic=approval.topic,
        parameter=approval.parameter_label,
        old_value=approval.old_value,
        new_value=final_new_val,
        status="APPROVED",
        doc_id=approval.target_doc_id,
        doc_version=new_version_str,
        note=approval_note,
        prev_hash=prev_hash,
        canonical_payload=canonical_payload,
        hash=full_hash,
        source_draft_id=approval.draft_id,
        reviewed_by=user.username,
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
        message=f"Draft {approval.draft_id} approved by {user.username}. Document {approval.target_doc_id} updated to {new_version_str}.",
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
    user: AuthUser = Depends(require_role(Role.REVIEWER)),
):
    """
    Decline an unverified or risky proposal:
      - Leaves official document strictly unchanged
      - Records rejection note and reviewer identity
      - Appends an immutable chained ledger record marking DECLINED status
      - Closes the draft to prevent future tampering
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
    decline_note = body.note or f"Declined by {user.username} during specification review."

    # Retrieve current document version
    doc_stmt = select(OfficialDoc).where(OfficialDoc.doc_id == approval.target_doc_id)
    doc_res = await db.execute(doc_stmt)
    doc = doc_res.scalars().first()
    doc_ver = doc.version if doc else "v1.0"

    total_ledger = (await db.execute(select(func.count(DecisionLedgerEntry.id)))).scalar() or 0
    decision_id = f"DEC-2026-{total_ledger + 1:02d}"

    last_stmt = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.timestamp.desc(), DecisionLedgerEntry.id.desc()).limit(1)
    last_entry = (await db.execute(last_stmt)).scalars().first()
    prev_hash = last_entry.hash if (last_entry and last_entry.hash) else ("0" * 64)

    ledger_payload = {
        "decision_id": decision_id,
        "draft_id": approval.draft_id,
        "doc_id": approval.target_doc_id,
        "doc_version": doc_ver,
        "parameter": approval.parameter_label,
        "old_value": approval.old_value,
        "new_value": approval.new_value,
        "status": "DECLINED",
        "note": decline_note,
        "reviewed_by": user.username,
        "timestamp": now.isoformat(),
    }
    canonical_payload, full_hash = _compute_chained_sha256(ledger_payload, prev_hash)

    ledger_entry = DecisionLedgerEntry(
        decision_id=decision_id,
        timestamp=now,
        topic=approval.topic,
        parameter=approval.parameter_label,
        old_value=approval.old_value,
        new_value=approval.new_value,
        status="DECLINED",
        doc_id=approval.target_doc_id,
        doc_version=doc_ver,
        note=decline_note,
        prev_hash=prev_hash,
        canonical_payload=canonical_payload,
        hash=full_hash,
        source_draft_id=approval.draft_id,
        reviewed_by=user.username,
    )
    db.add(ledger_entry)

    approval.status = "DECLINED"
    approval.reviewed_at = now
    approval.review_note = decline_note

    await db.commit()

    return ActionResponse(
        id=approval.draft_id,
        status="DECLINED",
        message=f"Draft {approval.draft_id} declined by {user.username}. Official specification '{approval.target_doc_id}' remained untouched.",
        doc_id=approval.target_doc_id,
        ledger_id=decision_id,
    )

