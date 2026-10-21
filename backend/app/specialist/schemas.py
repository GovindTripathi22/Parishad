"""
Parishad Review Specialist — Strict Output Contract & Input Schemas.
Conforms strictly to Phase 5 requirements with independent backend verification.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# ── Strict Finding & Evidence Schemas ─────────────────────────────────────────

class FindingEvidence(BaseModel):
    """Traceable citation to an official document, rule, or change request."""
    source: str = Field(..., description="Supplied document or rule identifier (e.g. DOC-BMS-402).")
    quote: Optional[str] = Field(None, description="Exact supporting text when available.")
    location: Optional[str] = Field(None, description="Section, heading, or line location when available.")


FindingCategory = Literal[
    "policy_conflict",
    "unit_mismatch",
    "tolerance_drift",
    "dependency_risk",
    "unverified_claim",
    "security_threat",
    "low_severity_improvement",
]

SeverityLevel = Literal["critical", "high", "medium", "low", "info"]
ConfidenceLevel = Literal["high", "medium", "low"]
OverallStatus = Literal["blocking", "needs_review", "no_known_conflict", "insufficient_evidence"]
ImprovementType = Literal["required_fix", "optional_improvement", "verification_needed"]


class ReviewFinding(BaseModel):
    """An individual finding produced or preserved by the specialist."""
    finding_id: str = Field(..., description="Stable ID supplied by Parishad (e.g. FIND-01).")
    category: FindingCategory = Field("policy_conflict", description="Classification of the finding.")
    severity: SeverityLevel = Field("medium", description="Severity: critical | high | medium | low | info.")
    title: str = Field(..., description="Clear finding title.")
    description: str = Field(..., description="What the evidence indicates.")
    evidence: list[FindingEvidence] = Field(default_factory=list, description="Citations supporting this finding.")
    impact: str = Field("", description="Why the finding matters.")
    recommended_fix: str = Field("", description="Specific corrective action.")
    confidence: ConfidenceLevel = Field("high", description="Confidence: high | medium | low.")
    is_inference: bool = Field(False, description="True if inferred rather than a confirmed fact.")
    needs_human_verification: bool = Field(True, description="True if human verification required.")


class ReviewImprovement(BaseModel):
    """Actionable recommendation proposing a fix or enhancement."""
    title: str = Field(..., description="Improvement title.")
    description: str = Field(..., description="Recommended improvement description.")
    linked_finding_ids: list[str] = Field(default_factory=list, description="Associated finding IDs.")
    type: ImprovementType = Field("required_fix", description="required_fix | optional_improvement | verification_needed.")


class ReviewCompleteness(BaseModel):
    """Independently computed and verified by backend code, NOT trusted from LLM."""
    input_finding_count: int = Field(0, description="Total findings provided in input.")
    output_finding_count: int = Field(0, description="Total findings present in report.")
    missing_finding_ids: list[str] = Field(default_factory=list, description="IDs dropped by model (must be empty).")
    duplicate_finding_ids: list[str] = Field(default_factory=list, description="Duplicate IDs found.")
    all_input_findings_preserved: bool = Field(True, description="True if 100% of input IDs are present.")


# ── Root Review Report Schema ─────────────────────────────────────────────────

class ParishadReviewReport(BaseModel):
    """The complete structured report produced by the Parishad Review Specialist."""
    schema_version: str = Field("1.0", description="Contract version.")
    review_title: str = Field(..., description="Short descriptive title.")
    overall_status: OverallStatus = Field(..., description="blocking | needs_review | no_known_conflict | insufficient_evidence.")
    executive_summary: str = Field(..., description="Crisp engineering executive summary.")
    findings: list[ReviewFinding] = Field(default_factory=list, description="All findings (100% preserved).")
    missing_information: list[str] = Field(default_factory=list, description="Gaps, missing data or test reports.")
    improvements: list[ReviewImprovement] = Field(default_factory=list, description="Recommended fixes and improvements.")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions made during analysis.")
    questions_for_reviewer: list[str] = Field(default_factory=list, description="Clarifying questions for human approver.")
    completeness: ReviewCompleteness = Field(default_factory=ReviewCompleteness, description="Backend-audited completeness record.")
    fallback_used: bool = Field(False, description="True if deterministic fallback was triggered.")


# ── Specialist Input Schema ───────────────────────────────────────────────────

class ParishadReviewInput(BaseModel):
    """Structured input payload assembled from upstream Parishad pipeline stages."""
    change_request: str = Field(..., description="Raw text of the proposed change.")
    source: str = Field("GitHub Pull Request", description="Origin source.")
    topic: str = Field("AUTO", description="Domain topic.")
    official_docs: list[dict[str, Any]] = Field(default_factory=list, description="Matched official documents and rules.")
    pipeline_findings: list[dict[str, Any]] = Field(default_factory=list, description="Upstream findings with stable IDs.")
    ledger_history: list[dict[str, Any]] = Field(default_factory=list, description="Historical decisions from decision ledger.")
    missing_context: list[str] = Field(default_factory=list, description="Identified pipeline gaps.")
    security_warnings: list[str] = Field(default_factory=list, description="Security guard alerts.")
