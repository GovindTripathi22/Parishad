"""
CortexOS — Official Documents Management (View 3)
CRUD endpoints for official company specifications and policies.
On document creation, automatically extracts numeric constraints into rules.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.connection import get_db
from app.database.models import DocRule, OfficialDoc
from app.extraction.fact_extractor import extract_facts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/docs", tags=["docs"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateDocRequest(BaseModel):
    title: str = Field(..., min_length=1)
    topic: Optional[str] = "GENERAL"
    content: str = Field(..., min_length=1)


class UpdateDocRequest(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    content: Optional[str] = None


def _format_doc(doc: OfficialDoc) -> dict[str, Any]:
    rules_formatted = []
    if doc.rules:
        for r in doc.rules:
            rules_formatted.append(f"{r.label}: {r.value} {r.unit.upper()}")

    return {
        "id": doc.doc_id,
        "doc_id": doc.doc_id,
        "title": doc.title,
        "topic": doc.topic,
        "version": doc.version,
        "content": doc.content,
        "rules": rules_formatted,
        "rules_detail": [{"label": r.label, "value": r.value, "unit": r.unit} for r in (doc.rules or [])],
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="List all official company documents",
)
async def list_docs(db: AsyncSession = Depends(get_db)):
    """Fetch all active official documents with their certified rules."""
    stmt = (
        select(OfficialDoc)
        .options(selectinload(OfficialDoc.rules))
        .where(OfficialDoc.is_active == True)
        .order_by(OfficialDoc.created_at.desc())
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [_format_doc(d) for d in docs]


@router.get(
    "/{doc_id}",
    summary="Get single official document",
)
async def get_doc(doc_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch a document by doc_id (e.g. 'DOC-BMS-402') or internal ID."""
    stmt = (
        select(OfficialDoc)
        .options(selectinload(OfficialDoc.rules))
        .where((OfficialDoc.doc_id == doc_id) | (OfficialDoc.id == doc_id))
    )
    result = await db.execute(stmt)
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{doc_id}' not found")
    return _format_doc(doc)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Add a new official document / specification",
)
async def create_doc(body: CreateDocRequest, db: AsyncSession = Depends(get_db)):
    """
    Creates a new official document, extracts numeric rules, and indexes it
    into the Company Brain.
    """
    total = (await db.execute(select(func.count(OfficialDoc.id)))).scalar() or 0
    new_doc_id = f"DOC-CUST-{100 + total + 1}"
    now = datetime.now(timezone.utc)

    topic = (body.topic or "GENERAL").strip().upper()

    doc = OfficialDoc(
        doc_id=new_doc_id,
        topic=topic,
        title=body.title.strip(),
        version="v1.0",
        content=body.content.strip(),
        source="user_upload",
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    db.add(doc)

    # Automatically extract facts and create DocRule entries (only for verified units)
    facts_res = extract_facts(body.content)
    if facts_res.has_signal:
        for f in facts_res.all_facts:
            if f.unit:
                rule = DocRule(
                    doc_id=new_doc_id,
                    label=f.label or "Specification Limit",
                    value=f.value,
                    unit=f.unit,
                )
                db.add(rule)

    await db.commit()

    # Re-fetch with loaded rules
    return await get_doc(new_doc_id, db)


@router.put(
    "/{doc_id}",
    summary="Update document content",
)
async def update_doc(
    doc_id: str,
    body: UpdateDocRequest,
    db: AsyncSession = Depends(get_db),
):
    """Direct administrative document update."""
    stmt = (
        select(OfficialDoc)
        .options(selectinload(OfficialDoc.rules))
        .where((OfficialDoc.doc_id == doc_id) | (OfficialDoc.id == doc_id))
    )
    result = await db.execute(stmt)
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if body.title:
        doc.title = body.title.strip()
    if body.topic:
        doc.topic = body.topic.strip().upper()
    if body.content:
        doc.content = body.content.strip()
        try:
            v_num = float(doc.version.replace("v", ""))
            doc.version = f"v{v_num + 0.1:.1f}"
        except Exception:
            doc.version = f"{doc.version}.1"

    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _format_doc(doc)


@router.delete(
    "/{doc_id}",
    summary="Deactivate (soft-delete) an official document",
)
async def delete_doc(doc_id: str, db: AsyncSession = Depends(get_db)):
    """Soft-deletes the document by setting is_active=False."""
    stmt = select(OfficialDoc).where((OfficialDoc.doc_id == doc_id) | (OfficialDoc.id == doc_id))
    result = await db.execute(stmt)
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    doc.is_active = False
    await db.commit()
    return {"message": f"Document {doc.doc_id} deactivated."}
