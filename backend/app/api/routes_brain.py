"""
CortexOS — Company Brain & Ask AI (View 3)
Source-grounded querying against official documents and decision history.
Uses hybrid search to retrieve context and Gemini to synthesize plain-English answers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database.connection import get_db
from app.database.models import BrainQuery, DecisionLedgerEntry, OfficialDoc, PendingApproval
from app.extraction.fact_extractor import extract_facts
from app.rag.hybrid_search import DEFAULT_DOCUMENTS, hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/brain", tags=["brain"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    query: Optional[str] = None
    question: Optional[str] = None

    def get_query(self) -> str:
        return (self.query or self.question or "").strip()


class DocHit(BaseModel):
    doc_id: str
    title: str
    topic: str = "GENERAL"
    snippet: str = ""
    score: float = 0.0


class AskResponse(BaseModel):
    answer: str
    response: str
    question: str
    query: str
    doc_hits: list[DocHit] = []
    pending_alert: Optional[str] = None
    query_id: Optional[str] = None


class QueryHistoryItem(BaseModel):
    id: str
    question: str
    answer: Optional[str] = None
    created_at: Optional[str] = None


class OcrRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/png"
    filename: Optional[str] = "spec_sheet.png"


class OcrResponse(BaseModel):
    title: str
    topic: str
    content: str
    extracted_rules: list[dict[str, Any]] = []
    status: str = "success"
    error_message: Optional[str] = None


# ── Answer Synthesizer ────────────────────────────────────────────────────────

async def _synthesize_answer(
    query: str,
    top_doc: dict[str, Any],
    pending_draft: Optional[PendingApproval],
    ledger_entries: list[DecisionLedgerEntry],
) -> str:
    """Generate source-grounded answer using Gemini or robust local synthesis."""
    settings = get_settings()
    doc_title = top_doc.get("title", "Official Specification")
    doc_id = top_doc.get("doc_id", "DOC")
    doc_content = top_doc.get("content", "")
    rules = top_doc.get("rules", [])
    rules_text = ", ".join(f"{r.get('label')}: {r.get('value')} {r.get('unit', '').upper()}" for r in rules) if rules else "No extracted rules"

    pending_info = ""
    if pending_draft:
        pending_info = (
            f" [ALERT: Pending draft {pending_draft.draft_id} proposes updating "
            f"{pending_draft.parameter_label} from {pending_draft.old_value} to {pending_draft.new_value}]."
        )

    # If Gemini is configured, call generative endpoint
    if settings.GEMINI_API_KEY:
        prompt = (
            f"You are the CortexOS Company Brain Assistant. Answer the question using ONLY the provided official context.\n"
            f"Official Document: {doc_title} ({doc_id})\n"
            f"Specification Content: {doc_content}\n"
            f"Certified Rules: {rules_text}\n"
            f"Pending Approvals: {pending_info}\n\n"
            f"Question: {query}\n\n"
            f"Provide a concise, direct, and factual answer in 2-3 sentences. Cite the document ID."
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
                    if text:
                        return text.strip()
        except Exception as exc:
            logger.warning("Gemini ask_brain call failed: %s", exc)

    # High-quality deterministic answer
    query_lower = query.lower()
    if "summarize" in query_lower or "history" in query_lower or "decision" in query_lower:
        if ledger_entries:
            summary_lines = [
                f"• {e.decision_id} ({e.topic}): {e.parameter} set to {e.new_value} [{e.status}] — {e.note}"
                for e in ledger_entries[:5]
            ]
            return f"Approved Decision Ledger Summary:\n" + "\n".join(summary_lines)
        return "No past decisions logged in the immutable ledger yet."

    return (
        f"According to {doc_title} ({doc_id}): \"{doc_content}\" "
        f"Certified parameters: {rules_text}.{pending_info}"
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/ask",
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask the Company Brain a natural-language question",
)
async def ask_brain(body: AskRequest, db: AsyncSession = Depends(get_db)):
    """
    Retrieves grounded context from official documents and decision history,
    then generates a factual, citation-backed answer.
    """
    q = body.get_query()
    if not q:
        return AskResponse(
            answer="Please enter a question to query the Company Brain.",
            response="Please enter a question to query the Company Brain.",
            question="",
            query="",
        )

    # 1. Load active docs
    stmt = select(OfficialDoc).options(selectinload(OfficialDoc.rules)).where(OfficialDoc.is_active == True)
    docs_db = (await db.execute(stmt)).scalars().all()

    docs_payload = []
    if docs_db:
        for d in docs_db:
            docs_payload.append({
                "doc_id": d.doc_id,
                "title": d.title,
                "topic": d.topic,
                "version": d.version,
                "content": d.content,
                "rules": [{"label": r.label, "value": r.value, "unit": r.unit} for r in d.rules],
            })
    else:
        docs_payload = DEFAULT_DOCUMENTS

    # 2. Hybrid search
    search = hybrid_search(q, documents=docs_payload, top_k=3)
    top_doc = search.results[0] if search.results else docs_payload[0]

    # 3. Check for any pending drafts on this document
    top_doc_id = top_doc.get("doc_id", "")
    p_stmt = select(PendingApproval).where(
        PendingApproval.target_doc_id == top_doc_id,
        PendingApproval.status == "PENDING",
    )
    pending_draft = (await db.execute(p_stmt)).scalars().first()

    # 4. Fetch recent ledger history
    l_stmt = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.timestamp.desc()).limit(5)
    ledger_entries = (await db.execute(l_stmt)).scalars().all()

    # 5. Synthesize answer
    answer = await _synthesize_answer(q, top_doc, pending_draft, ledger_entries)

    # 6. Audit log in BrainQuery
    query_record = BrainQuery(
        question=q,
        answer=answer,
        matched_doc_ids=[top_doc_id],
        confidence=top_doc.get("score", 0.95),
        created_at=datetime.now(timezone.utc),
    )
    db.add(query_record)
    await db.commit()
    await db.refresh(query_record)

    doc_hits = [
        DocHit(
            doc_id=r.get("doc_id", ""),
            title=r.get("title", ""),
            topic=r.get("topic", "GENERAL"),
            snippet=r.get("snippet", ""),
            score=r.get("score", 0.0),
        )
        for r in search.results
    ]

    pending_alert = None
    if pending_draft:
        pending_alert = f"Draft {pending_draft.draft_id} is currently pending review for this document."

    return AskResponse(
        answer=answer,
        response=answer,
        question=q,
        query=q,
        doc_hits=doc_hits,
        pending_alert=pending_alert,
        query_id=query_record.id,
    )


@router.get(
    "/history",
    summary="Query audit history",
)
async def brain_history(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return recently answered questions."""
    stmt = select(BrainQuery).order_by(BrainQuery.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "question": r.question,
            "answer": r.answer,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post(
    "/ocr",
    response_model=OcrResponse,
    status_code=status.HTTP_200_OK,
    summary="Extract specification rules from image using multimodal AI vision",
)
async def ocr_spec_sheet(body: OcrRequest):
    """
    Parses a specification sheet image via Gemini multimodal vision.
    If unconfigured or failed, returns an explicit OCR_UNAVAILABLE / OCR_FAILED
    status rather than fabricating a false sample specification.
    """
    settings = get_settings()
    filename_clean = (body.filename or "Scanned_Specification").rsplit(".", 1)[0].replace("_", " ").title()

    if not settings.GEMINI_API_KEY:
        return OcrResponse(
            title=filename_clean,
            topic="UNPROCESSED",
            content=f"[OCR_UNAVAILABLE] Specification image '{body.filename}' was uploaded, but GEMINI_API_KEY is not configured.",
            extracted_rules=[],
            status="OCR_UNAVAILABLE",
            error_message="Multimodal vision provider is unconfigured. Set GEMINI_API_KEY in .env to enable live image OCR.",
        )

    title = filename_clean
    topic = "OCR-SPEC"
    content = ""
    ocr_success = False
    last_error = ""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Extract the document title, short uppercase topic code, and all specification rules/numbers from this image into JSON."},
                    {"inlineData": {"mimeType": body.mime_type, "data": body.image_base64}}
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "topic": {"type": "STRING"},
                        "content": {"type": "STRING"}
                    },
                    "propertyOrdering": ["title", "topic", "content"]
                }
            }
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                import json
                raw_text = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                if raw_text:
                    parsed = json.loads(raw_text)
                    title = parsed.get("title", title)
                    topic = (parsed.get("topic", topic) or "OCR-SPEC").upper()
                    content = parsed.get("content", "")
                    if content:
                        ocr_success = True
            else:
                last_error = f"API returned status {res.status_code}"
    except Exception as exc:
        logger.warning("Gemini Vision OCR error: %s", exc)
        last_error = str(exc)

    if not ocr_success:
        return OcrResponse(
            title=filename_clean,
            topic="UNPROCESSED",
            content=f"[OCR_FAILED] Failed to extract text from {body.filename}: {last_error or 'Vision model returned no text'}",
            extracted_rules=[],
            status="OCR_FAILED",
            error_message=f"Vision extraction failed: {last_error or 'Vision model returned no text'}",
        )

    facts = extract_facts(content)
    rules = [{"unit": f.unit, "label": f.label, "value": f.value} for f in facts.all_facts]

    return OcrResponse(
        title=title,
        topic=topic,
        content=content,
        extracted_rules=rules,
        status="success",
    )

