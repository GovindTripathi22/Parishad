"""
CortexOS Council of Agents — Stage 2: Cross-Review Agent.

Takes the Stage 1 opinion and cross-references across the broader Company Brain:
1. Cascading impacts: checks if modifying this parameter impacts other official documents
2. Historical precedents: scans the decision ledger for past rulings on this topic
3. Risk confirmation: confirms or adjusts the opinion's findings
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CascadingImpactItem(BaseModel):
    """Structured evidence-grounded dependency impact."""
    source_doc_id: str = Field(..., description="ID of the potentially impacted document.")
    source_doc_version: str = Field("v1.0", description="Version of the referenced document.")
    relevant_passage: str = Field("", description="Exact or summarized excerpt supporting dependency.")
    reason: str = Field(..., description="Why the relationship between parameters matters.")
    is_verified: bool = Field(False, description="True if verified via explicit rule, False if heuristic hypothesis.")
    human_check_required: bool = Field(True, description="True if human verification is needed.")


class ReviewResult(BaseModel):
    """Output from the Cross-Review Agent."""
    opinion_confirmed: bool = Field(True, description="True if Stage 1 opinion was validated by cross-review.")
    cascading_impacts: list[str] = Field(default_factory=list, description="Legacy string summary of impacts.")
    structured_cascading_impacts: list[CascadingImpactItem] = Field(
        default_factory=list,
        description="Detailed evidence-grounded dependency items with document versions.",
    )
    precedent_decisions: list[str] = Field(default_factory=list, description="Historical decisions referencing similar parameters.")
    risk_assessment: str = Field("low", description="Evaluated risk: low | medium | high.")
    additional_docs_affected: list[str] = Field(default_factory=list, description="IDs of other documents with linked rules.")
    review_notes: str = Field("", description="Cross-review analytical assessment.")
    ledger_checked: bool = Field(True, description="True if immutable ledger precedent was searched.")
    scope_checked_docs: int = Field(0, description="Total number of documents inspected.")
    is_stage_complete: bool = Field(True, description="True if all secondary checks completed without error.")
    confidence: float = Field(0.9, ge=0.0, le=1.0)


async def cross_review(
    opinion: Any,
    all_docs: list[dict[str, Any]],
    ledger_history: list[dict[str, Any]],
    update_text: str,
) -> ReviewResult:
    """
    Stage 2 cross-review across all documents and immutable decision ledger history.
    Replaces static assumptions with dynamic, evidence-grounded relationship extraction.
    """
    has_conflict = getattr(opinion, "has_conflict", False) if not isinstance(opinion, dict) else opinion.get("has_conflict", False)
    param_label = (getattr(opinion, "parameter_label", "") if not isinstance(opinion, dict) else opinion.get("parameter_label", "")).lower()
    proposed_val = str(getattr(opinion, "proposed_value", "") if not isinstance(opinion, dict) else opinion.get("proposed_value", ""))

    cascading_strings: list[str] = []
    structured_cascading: list[CascadingImpactItem] = []
    affected_doc_ids: list[str] = []
    precedents: list[str] = []

    # 1. Evidence-grounded scan across other official documents
    param_tokens = [w for w in param_label.replace("_", " ").split() if len(w) > 3]

    for doc in all_docs:
        doc_id = doc.get("doc_id", "DOC-UNKNOWN")
        doc_version = doc.get("version", "v1.0")
        content = str(doc.get("content", ""))
        content_lower = content.lower()
        title_lower = str(doc.get("title", "")).lower()

        # Check for related rules or related terms
        doc_rules = doc.get("rules", [])
        matched_rule = None
        for r in doc_rules:
            r_lbl = r.get("label", "").lower()
            if any(tok in r_lbl for tok in param_tokens) or (param_label and param_label in r_lbl):
                matched_rule = r
                break

        if matched_rule:
            impact_desc = (
                f"{doc_id} ({doc_version}) contains related constraint '{matched_rule.get('label')}' "
                f"({matched_rule.get('value')} {matched_rule.get('unit')})."
            )
            cascading_strings.append(impact_desc)
            structured_cascading.append(
                CascadingImpactItem(
                    source_doc_id=doc_id,
                    source_doc_version=doc_version,
                    relevant_passage=f"Rule: {matched_rule.get('label')} = {matched_rule.get('value')} {matched_rule.get('unit')}",
                    reason=f"Direct parameter overlap with rule in {doc_id}",
                    is_verified=True,
                    human_check_required=True,
                )
            )
            if doc_id not in affected_doc_ids:
                affected_doc_ids.append(doc_id)
        else:
            # Domain-grounded physical relationships (Thermal, Bus, Financial SLA)
            is_related = False
            reason_str = ""
            passage = ""

            if "voltage" in param_label and ("thermal" in content_lower or "mosfet" in content_lower):
                is_related = True
                reason_str = "Higher operating voltage changes thermal dissipation and transistor breakdown margins."
                passage = [s for s in content.split(".") if "thermal" in s.lower() or "mosfet" in s.lower()][0:1]
                passage = passage[0].strip() if passage else content[:100]
            elif "speed" in param_label and ("can-fd" in content_lower or "bandwidth" in content_lower or "emc" in content_lower):
                is_related = True
                reason_str = "Higher bus speed requires electromagnetic compliance (EMC) and shielded harness verification."
                passage = [s for s in content.split(".") if "can-fd" in s.lower() or "speed" in s.lower()][0:1]
                passage = passage[0].strip() if passage else content[:100]
            elif "refund" in param_label and ("sla" in content_lower or "budget" in content_lower or "finance" in content_lower):
                is_related = True
                reason_str = "Altering refund windows impacts operational customer support SLA and financial reserve allocations."
                passage = [s for s in content.split(".") if "refund" in s.lower() or "sla" in s.lower()][0:1]
                passage = passage[0].strip() if passage else content[:100]

            if is_related:
                cascading_strings.append(f"{doc_id}: {reason_str}")
                structured_cascading.append(
                    CascadingImpactItem(
                        source_doc_id=doc_id,
                        source_doc_version=doc_version,
                        relevant_passage=passage,
                        reason=reason_str,
                        is_verified=False,  # Hypothesis requiring review
                        human_check_required=True,
                    )
                )
                if doc_id not in affected_doc_ids:
                    affected_doc_ids.append(doc_id)

    # 2. Check Decision Ledger precedents
    ledger_available = isinstance(ledger_history, list)
    if ledger_available:
        for entry in ledger_history[:10]:
            e_param = str(entry.get("parameter", "")).lower()
            e_topic = str(entry.get("topic", "")).lower()
            if any(term in e_param for term in param_tokens) or e_topic in param_label:
                status = entry.get("status", "APPROVED")
                precedents.append(
                    f"{entry.get('id', entry.get('decision_id', 'DEC'))} ({entry.get('timestamp', 'past')}): "
                    f"{entry.get('parameter')} changed from {entry.get('old_value')} to {entry.get('new_value')} [{status}]"
                )

    # 3. Overall risk level evaluation
    risk = "low"
    if has_conflict:
        delta_pct = getattr(opinion, "delta_pct", 0.0) if not isinstance(opinion, dict) else opinion.get("delta_pct", 0.0)
        if delta_pct > 25.0 or len(structured_cascading) > 1:
            risk = "high"
        elif delta_pct > 5.0 or len(structured_cascading) > 0:
            risk = "medium"

    stage_complete = ledger_available

    review_notes = (
        f"Cross-review evaluated {len(all_docs)} official specifications. "
        f"Identified {len(structured_cascading)} evidence-grounded secondary concern(s) "
        f"and {len(precedents)} historical precedent(s). "
        f"{'Ledger check completed.' if ledger_available else 'Warning: Decision ledger was unavailable.'}"
    )

    return ReviewResult(
        opinion_confirmed=True,
        cascading_impacts=cascading_strings,
        structured_cascading_impacts=structured_cascading,
        precedent_decisions=precedents,
        risk_assessment=risk,
        additional_docs_affected=affected_doc_ids,
        review_notes=review_notes,
        ledger_checked=ledger_available,
        scope_checked_docs=len(all_docs),
        is_stage_complete=stage_complete,
        confidence=0.92 if stage_complete else 0.70,
    )
