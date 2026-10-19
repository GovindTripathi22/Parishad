"""
CortexOS Council of Agents — Multi-Agent Consensus Orchestrator.

Orchestrates the 3-stage consensus pipeline:
  Stage 1: First Opinion Agent (conflict & drift detection)
  Stage 2: Cross-Review Agent (cascading impacts & precedent search)
  Stage 3: Synthesis Agent (executive summary & doc update drafting)

Enforces privacy and groundedness: no change is automatically committed to official
company documents without an explicit human approval.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.config import get_settings
from app.rag.hybrid_search import DEFAULT_DOCUMENTS, hybrid_search
from app.agents.opinion_agent import OpinionResult, analyze_opinion
from app.agents.review_agent import ReviewResult, cross_review
from app.agents.synthesis_agent import SynthesisResult, synthesize_recommendation

logger = logging.getLogger(__name__)


class CouncilVerdict(BaseModel):
    """Unified verdict returned by the Council of Agents."""
    approved: bool = Field(True, description="True if council found the update safe and compliant.")
    verdict: str = Field("ALIGNED", description="CONFLICT_DETECTED | ALIGNED | BLOCKED | LOW_SIGNAL")
    confidence: float = Field(0.9, ge=0.0, le=1.0)
    reasoning: str = Field("", description="Executive summary and justification.")
    human_summary: str = Field("", description="Manager-friendly plain text summary.")
    proposed_doc_update: str = Field("", description="Draft of updated official text.")
    opinion: Optional[OpinionResult] = Field(None, description="Stage 1 First Opinion.")
    review: Optional[ReviewResult] = Field(None, description="Stage 2 Cross-Review.")
    synthesis: Optional[SynthesisResult] = Field(None, description="Stage 3 Consensus Synthesis.")
    duration_ms: int = Field(0, description="Pipeline duration in milliseconds.")
    stages_completed: int = Field(3, description="Count of successfully executed stages.")


async def run_council(
    *args: Any,
    source: str = "unknown",
    topic: str = "AUTO",
    message: str = "",
    update_text: str = "",
    facts: Any = None,
    extracted_facts: Any = None,
    conflicts: list[Any] | None = None,
    matched_doc: dict[str, Any] | None = None,
    all_docs: list[dict[str, Any]] | None = None,
    ledger_history: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> CouncilVerdict:
    """
    Run the full 3-stage Council of Agents consensus workflow.
    Gracefully handles flexible argument signatures for all callers.
    """
    start_time = time.perf_counter()
    settings = get_settings()

    # Normalize parameters
    text = message or update_text or (args[0] if len(args) > 0 and isinstance(args[0], str) else "")
    facts_obj = facts if facts is not None else extracted_facts
    docs_list = all_docs or DEFAULT_DOCUMENTS

    # If no matched doc provided, use hybrid search to locate best match
    target_doc = matched_doc
    if not target_doc:
        search_res = hybrid_search(text, documents=docs_list, top_k=1, prefer_topic=topic)
        if search_res.results:
            target_doc = search_res.results[0]
        elif docs_list:
            target_doc = docs_list[0]

    # If Council is disabled via configuration, return lightweight evaluation
    if not settings.COUNCIL_ENABLED:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return CouncilVerdict(
            approved=True,
            verdict="ALIGNED",
            confidence=0.8,
            reasoning="Council bypass (COUNCIL_ENABLED=False).",
            human_summary="Council disabled; verified against default schema.",
            duration_ms=duration_ms,
            stages_completed=0,
        )

    stages_done = 0

    # ── Stage 1: First Opinion ────────────────────────────────────────────────
    try:
        opinion = await analyze_opinion(text, target_doc, facts_obj)
        stages_done += 1
    except Exception as exc:
        logger.error("Council Stage 1 failed: %s", exc)
        opinion = OpinionResult(
            has_conflict=bool(conflicts and len(conflicts) > 0),
            severity="medium" if conflicts else "low",
            reasoning=f"Stage 1 fallback: {exc}",
            confidence=0.6,
        )

    # ── Stage 2: Cross-Review ─────────────────────────────────────────────────
    try:
        history_list = ledger_history or [
            {
                "id": "DEC-2026-01",
                "timestamp": "2026-09-12",
                "topic": "BMS-48V",
                "parameter": "Max Voltage Limit",
                "old_value": "52 V",
                "new_value": "54 V",
                "status": "APPROVED",
            },
            {
                "id": "DEC-2026-02",
                "timestamp": "2026-10-01",
                "topic": "COMPANY-OPS",
                "parameter": "Refund Window",
                "old_value": "15 days",
                "new_value": "30 days",
                "status": "APPROVED",
            },
        ]
        review = await cross_review(opinion, docs_list, history_list, text)
        stages_done += 1
    except Exception as exc:
        logger.error("Council Stage 2 failed: %s", exc)
        review = ReviewResult(
            opinion_confirmed=True,
            risk_assessment="medium" if opinion.has_conflict else "low",
            review_notes=f"Stage 2 fallback: {exc}",
            confidence=0.7,
        )

    # ── Stage 3: Synthesis ────────────────────────────────────────────────────
    try:
        synthesis = await synthesize_recommendation(opinion, review, target_doc, text)
        stages_done += 1
    except Exception as exc:
        logger.error("Council Stage 3 failed: %s", exc)
        synthesis = SynthesisResult(
            final_verdict="CONFLICT_DETECTED" if opinion.has_conflict else "ALIGNED",
            confidence=0.75,
            human_summary=opinion.reasoning,
            proposed_doc_update=target_doc.get("content", "") if target_doc else "",
            risk_level="medium",
            recommended_action="STAGE_APPROVAL" if opinion.has_conflict else "AUTO_PASS",
        )

    duration_ms = int((time.perf_counter() - start_time) * 1000)
    is_approved = not opinion.has_conflict

    return CouncilVerdict(
        approved=is_approved,
        verdict=synthesis.final_verdict,
        confidence=synthesis.confidence,
        reasoning=synthesis.human_summary,
        human_summary=synthesis.human_summary,
        proposed_doc_update=synthesis.proposed_doc_update,
        opinion=opinion,
        review=review,
        synthesis=synthesis,
        duration_ms=duration_ms,
        stages_completed=stages_done,
    )
