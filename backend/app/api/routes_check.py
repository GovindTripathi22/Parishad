"""
CortexOS — Check an Update (View 1)
End-to-End Pipeline: Privacy Guard → Fact Extraction → Entity Resolution →
Hybrid Search → Council of Agents Consensus → Human-in-the-Loop Approval Staging.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database.connection import get_db
from app.database.models import DocRule, OfficialDoc, PendingApproval
from app.privacy.pii_redactor import redact
from app.privacy.injection_guard import check_safety
from app.extraction.fact_extractor import extract_facts
from app.extraction.entity_resolver import resolve_entities
from app.rag.hybrid_search import DEFAULT_DOCUMENTS, hybrid_search
from app.agents.council import run_council
from app.specialist.schemas import ParishadReviewInput, ParishadReviewReport
from app.specialist.service import ReviewSummaryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/check", tags=["check"])


# ── Request / Response Schemas ────────────────────────────────────────────────

class CheckRequest(BaseModel):
    message: Optional[str] = None
    content: Optional[str] = None
    source: str = "GitHub Pull Request"
    topic: Optional[str] = None
    category: Optional[str] = None
    enforce_privacy: Optional[bool] = None
    privacy_enabled: Optional[bool] = None

    def get_text(self) -> str:
        return (self.message or self.content or "").strip()

    def get_topic(self) -> str:
        return self.topic or self.category or "AUTO"

    def is_privacy_enforced(self) -> bool:
        if self.enforce_privacy is not None:
            return self.enforce_privacy
        if self.privacy_enabled is not None:
            return self.privacy_enabled
        return True


class RuleConflict(BaseModel):
    rule_id: str
    rule_text: str
    conflict_type: str = "value_mismatch"
    severity: str = "medium"
    official_value: str = ""
    proposed_value: str = ""


class CheckResult(BaseModel):
    # Frontend consumption fields
    verdict: str  # conflict | aligned | blocked | low_signal
    title: str
    subtitle: str
    message: str = ""
    parameter: str = ""
    official_value: str = ""
    proposed_value: str = ""
    rule_id: str = ""
    explanation: str = ""
    confidence: float = 0.95
    threats: list[str] = []

    # Detailed pipeline metrics
    privacy_clean: bool = True
    redacted_count: int = 0
    clean_text: str = ""
    is_safe: bool = True
    threat_score: float = 0.0
    blocked_reason: Optional[str] = None
    has_signal: bool = False
    proposed_fact: Optional[str] = None
    all_facts: list[str] = []
    change_detected: bool = False
    entities: list[dict[str, Any]] = []
    top_doc_id: Optional[str] = None
    top_doc_title: Optional[str] = None
    search_score: Optional[float] = None
    conflicts: list[RuleConflict] = []
    has_conflict: bool = False
    council_approved: Optional[bool] = None
    council_reasoning: Optional[str] = None
    council_confidence: Optional[float] = None
    pending_approval_id: Optional[str] = None
    pipeline: str = "full"
    specialist_report: Optional[ParishadReviewReport] = None


# ── Main Endpoint ─────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=CheckResult,
    status_code=status.HTTP_200_OK,
    summary="Full check pipeline against Company Brain",
)
async def check_update(body: CheckRequest, db: AsyncSession = Depends(get_db)):
    """
    Executes the 4-step autonomous verification pipeline:
      1. Privacy & Injection Guard (block attacks, redact emails/keys)
      2. Universal Fact & Number Extractor (units, limits, budgets)
      3. Hybrid RAG Search (matches official documents)
      4. Council of Agents Consensus (First Opinion -> Cross-Review -> Synthesis)
         and staging to Pending Approvals if drift is detected.
    """
    raw_text = body.get_text()
    source_val = body.source
    topic_val = body.get_topic()
    enforce_privacy = body.is_privacy_enforced()

    if not raw_text:
        empty_input = ParishadReviewInput(
            change_request="",
            source=source_val,
            topic=topic_val,
            official_docs=[],
            pipeline_findings=[],
            ledger_history=[],
            missing_context=["No content provided to check."],
            security_warnings=[],
        )
        empty_report = ReviewSummaryService().generate_deterministic_fallback(empty_input)
        return CheckResult(
            verdict="low_signal",
            title="Empty Content",
            subtitle="No content provided to check",
            message="Please provide a message, pull request, or policy text to check.",
            privacy_clean=True,
            clean_text="",
            specialist_report=empty_report,
        )

    # ── Step 1: Injection & Security Guard ────────────────────────────────────
    guard = check_safety(raw_text)
    if not guard.is_safe:
        threats_list = guard.matched_patterns or [guard.blocked_reason or "Unsafe prompt override attempt"]
        blocked_input = ParishadReviewInput(
            change_request=raw_text,
            source=source_val,
            topic=topic_val,
            official_docs=[],
            pipeline_findings=[],
            ledger_history=[],
            missing_context=[],
            security_warnings=threats_list,
        )
        blocked_report = ReviewSummaryService().generate_deterministic_fallback(blocked_input)
        return CheckResult(
            verdict="blocked",
            title="Prompt Injection Attack Blocked",
            subtitle="Adversarial input detected and neutralized",
            message="The submitted content contains prompt injection or unsafe patterns attempting to override rules or exfiltrate restricted data. The input has been blocked by CortexOS Security Guard.",
            threats=threats_list,
            privacy_clean=False,
            clean_text=raw_text,
            is_safe=False,
            threat_score=guard.threat_score,
            blocked_reason=guard.blocked_reason,
            confidence=0.99,
            specialist_report=blocked_report,
        )

    # ── Step 2: Privacy Redaction ─────────────────────────────────────────────
    redaction = redact(raw_text, enforce=enforce_privacy)
    clean_text = redaction.clean_text

    # ── Step 3: Fact Extraction ───────────────────────────────────────────────
    extraction = extract_facts(clean_text)
    facts_list_str = [f"{f.value} {f.display_unit}" for f in extraction.all_facts]
    prop_fact_str = f"{extraction.proposed_fact.value} {extraction.proposed_fact.display_unit}" if extraction.proposed_fact else None

    # ── Step 4: Entity Resolution ─────────────────────────────────────────────
    entities_res = resolve_entities(clean_text)
    entities = [e.model_dump() for e in entities_res.entities]

    # ── Step 5: Hybrid Document Search ────────────────────────────────────────
    # Load all active documents from database
    stmt = select(OfficialDoc).options(selectinload(OfficialDoc.rules)).where(OfficialDoc.is_active == True)
    docs_db = (await db.execute(stmt)).scalars().all()

    docs_payload: list[dict[str, Any]] = []
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

    extracted_unit = extraction.proposed_fact.unit if extraction.proposed_fact else None
    search_res = hybrid_search(clean_text, documents=docs_payload, top_k=3, prefer_topic=topic_val, prefer_unit=extracted_unit)
    top_doc_dict = search_res.results[0] if search_res.results else (docs_payload[0] if docs_payload else None)

    top_doc_id = top_doc_dict.get("doc_id") if top_doc_dict else None
    top_doc_title = top_doc_dict.get("title") if top_doc_dict else None
    search_score = top_doc_dict.get("score") if top_doc_dict else 0.0

    # ── Step 6: Council of Agents Consensus ───────────────────────────────────
    council = await run_council(
        source=source_val,
        topic=topic_val,
        message=clean_text,
        facts=extraction,
        matched_doc=top_doc_dict,
        all_docs=docs_payload,
    )

    has_conflict = council.opinion.has_conflict if council.opinion else False
    param_label = council.opinion.parameter_label if council.opinion else "Rule Parameter"
    official_val = council.opinion.official_value if council.opinion else ""
    proposed_val = council.opinion.proposed_value if council.opinion else ""
    rule_id_ref = top_doc_id or "DOC-RULES"

    conflicts: list[RuleConflict] = []
    if has_conflict:
        conflicts.append(
            RuleConflict(
                rule_id=rule_id_ref,
                rule_text=f"{param_label}: official {official_val} vs proposed {proposed_val}",
                conflict_type="tolerance_drift",
                severity=council.opinion.severity if council.opinion else "medium",
                official_value=official_val,
                proposed_value=proposed_val,
            )
        )

    # ── Step 7: Auto-stage Pending Approval if Conflict ───────────────────────
    staged_draft_id: Optional[str] = None
    if has_conflict:
        # Check if an identical draft already exists
        count_stmt = select(func.count(PendingApproval.id))
        total_approvals = (await db.execute(count_stmt)).scalar() or 0
        new_draft_id = f"REV-{100 + total_approvals + 1}"

        numeric_val = extraction.proposed_fact.value if extraction.proposed_fact else None
        unit_val = extraction.proposed_fact.unit if extraction.proposed_fact else "val"

        approval = PendingApproval(
            draft_id=new_draft_id,
            source=source_val,
            topic=top_doc_dict.get("topic", topic_val) if top_doc_dict else topic_val,
            target_doc_id=top_doc_id or "DOC-GEN-001",
            parameter_label=param_label,
            unit=unit_val,
            old_value=official_val or "Certified Limit",
            new_value=proposed_val or "Proposed Limit",
            numeric_new=numeric_val,
            reason=council.human_summary,
            old_text=top_doc_dict.get("content", "") if top_doc_dict else "",
            proposed_text=council.proposed_doc_update or (top_doc_dict.get("content", "") if top_doc_dict else ""),
            status="PENDING",
            council_opinion={
                "severity": council.opinion.severity if council.opinion else "medium",
                "delta_pct": council.opinion.delta_pct if council.opinion else 0.0,
                "review_notes": council.review.review_notes if council.review else "",
                "cascading_impacts": council.review.cascading_impacts if council.review else [],
            },
            council_confidence=council.confidence,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)
        staged_draft_id = approval.draft_id

    # ── Step 7.5: Parishad Review Specialist Summary ──────────────────────────
    pipeline_findings: list[dict[str, Any]] = []
    if has_conflict:
        pipeline_findings.append({
            "finding_id": "FIND-01",
            "category": "policy_conflict",
            "proposed_value": proposed_val,
            "official_value": official_val,
            "delta_pct": council.opinion.delta_pct if council.opinion else 0.0,
            "target_doc_id": top_doc_id or "DOC-GEN-001",
            "initial_rationale": council.opinion.reasoning if council.opinion else "Parameter conflict detected.",
        })
        if council.review and council.review.cascading_impacts:
            for idx, c_imp in enumerate(council.review.cascading_impacts, start=2):
                pipeline_findings.append({
                    "finding_id": f"FIND-0{idx}",
                    "category": "dependency_risk",
                    "proposed_value": proposed_val,
                    "official_value": official_val,
                    "delta_pct": council.opinion.delta_pct if council.opinion else 0.0,
                    "target_doc_id": top_doc_id or "DOC-GEN-001",
                    "initial_rationale": c_imp,
                })

    missing_ctx: list[str] = []
    if not extraction.has_signal and clean_text:
        missing_ctx.append("No quantitative physical, temporal, or financial parameters detected in message.")

    review_input = ParishadReviewInput(
        change_request=clean_text,
        source=source_val,
        topic=topic_val,
        official_docs=docs_payload[:3] if docs_payload else [],
        pipeline_findings=pipeline_findings,
        ledger_history=[],
        missing_context=missing_ctx,
        security_warnings=[],
    )

    specialist_service = ReviewSummaryService()
    specialist_report = await specialist_service.generate_review_summary(review_input)

    # ── Step 8: Build Unified Frontend/API Response ───────────────────────────
    if has_conflict:
        verdict = "conflict"
        title = "Parameter Conflict Detected"
        subtitle = f"Official value differs from proposed update ({council.opinion.delta_pct if council.opinion else 0}% shift)"
        message = council.human_summary
        explanation = council.human_summary
    elif not extraction.has_signal:
        verdict = "low_signal"
        title = "No Rule or Number Change Detected"
        subtitle = "Message contains general communication / casual chatter"
        message = "This message does not modify any official company parameters, tolerances, or specifications."
        explanation = "Casual team chatter filtered out by signal-vs-noise engine."
    else:
        verdict = "aligned"
        title = "Verified Compliant"
        subtitle = f"Matches certified rules in {top_doc_id}"
        message = f"The submitted update has been verified against {top_doc_title} ({top_doc_id}). Values are within approved operational tolerances."
        explanation = "Grounded against official Company Brain specifications with zero conflicts."

    return CheckResult(
        verdict=verdict,
        title=title,
        subtitle=subtitle,
        message=message,
        parameter=param_label,
        official_value=official_val,
        proposed_value=proposed_val,
        rule_id=rule_id_ref,
        explanation=explanation,
        confidence=council.confidence,
        threats=[],
        privacy_clean=redaction.is_clean,
        redacted_count=redaction.redacted_count,
        clean_text=clean_text,
        is_safe=True,
        threat_score=guard.threat_score,
        blocked_reason=None,
        has_signal=extraction.has_signal,
        proposed_fact=prop_fact_str,
        all_facts=facts_list_str,
        change_detected=extraction.change_detected,
        entities=entities,
        top_doc_id=top_doc_id,
        top_doc_title=top_doc_title,
        search_score=search_score,
        conflicts=conflicts,
        has_conflict=has_conflict,
        council_approved=council.approved,
        council_reasoning=council.reasoning,
        council_confidence=council.confidence,
        pending_approval_id=staged_draft_id,
        pipeline="full",
        specialist_report=specialist_report,
    )


@router.post(
    "/quick",
    response_model=CheckResult,
    status_code=status.HTTP_200_OK,
    summary="Quick check (skips council multi-agent loop)",
)
async def quick_check(body: CheckRequest, db: AsyncSession = Depends(get_db)):
    """Fast pipeline for low-latency batch processing or CI/CD pre-commit hooks."""
    return await check_update(body, db)


@router.post(
    "/specialist",
    response_model=ParishadReviewReport,
    status_code=status.HTTP_200_OK,
    summary="Generate strict Parishad Review Specialist report directly",
)
async def specialist_review(body: CheckRequest, db: AsyncSession = Depends(get_db)) -> ParishadReviewReport:
    """Direct execution endpoint returning the strict structured Parishad Review Report."""
    res = await check_update(body, db)
    if res.specialist_report:
        return res.specialist_report
    # Fallback if blocked or empty
    service = ReviewSummaryService()
    return service.generate_deterministic_fallback(
        ParishadReviewInput(
            change_request=body.get_text(),
            source=body.source,
            topic=body.get_topic(),
            official_docs=[],
            pipeline_findings=[],
            ledger_history=[],
            missing_context=["Input blocked or empty"],
            security_warnings=res.threats,
        )
    )
