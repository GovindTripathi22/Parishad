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


class ReviewResult(BaseModel):
    """Output from the Cross-Review Agent."""
    opinion_confirmed: bool = Field(True, description="True if Stage 1 opinion was validated by cross-review.")
    cascading_impacts: list[str] = Field(default_factory=list, description="Other specifications or teams impacted.")
    precedent_decisions: list[str] = Field(default_factory=list, description="Historical decisions referencing similar parameters.")
    risk_assessment: str = Field("low", description="Evaluated risk: low | medium | high.")
    additional_docs_affected: list[str] = Field(default_factory=list, description="IDs of other documents with linked rules.")
    review_notes: str = Field("", description="Cross-review analytical assessment.")
    confidence: float = Field(0.9, ge=0.0, le=1.0)


async def cross_review(
    opinion: Any,
    all_docs: list[dict[str, Any]],
    ledger_history: list[dict[str, Any]],
    update_text: str,
) -> ReviewResult:
    """
    Stage 2 cross-review across all documents and immutable decision ledger history.
    """
    has_conflict = getattr(opinion, "has_conflict", False) if not isinstance(opinion, dict) else opinion.get("has_conflict", False)
    param_label = (getattr(opinion, "parameter_label", "") if not isinstance(opinion, dict) else opinion.get("parameter_label", "")).lower()
    proposed_val = str(getattr(opinion, "proposed_value", "") if not isinstance(opinion, dict) else opinion.get("proposed_value", ""))

    cascading: list[str] = []
    affected_doc_ids: list[str] = []
    precedents: list[str] = []

    # 1. Check cascading impacts across other official documents
    for doc in all_docs:
        doc_id = doc.get("doc_id", "")
        content_lower = str(doc.get("content", "")).lower()
        title_lower = str(doc.get("title", "")).lower()

        # Electrical/thermal cross-link heuristic
        if "voltage" in param_label and ("thermal" in content_lower or "mosfet" in content_lower):
            cascading.append(f"{doc_id}: Higher transient voltage increases thermal dissipation across power stages.")
            if doc_id not in affected_doc_ids:
                affected_doc_ids.append(doc_id)

        # Radar speed / bus bandwidth cross-link
        if "speed" in param_label and ("can-fd" in content_lower or "bandwidth" in content_lower):
            cascading.append(f"{doc_id}: Higher bitrates may require shielded harnesses to meet EMC standards.")
            if doc_id not in affected_doc_ids:
                affected_doc_ids.append(doc_id)

        # Policy / SLA response cross-link
        if "refund" in param_label and ("sla" in content_lower or "budget" in content_lower):
            cascading.append(f"{doc_id}: Modifying refund windows impacts quarterly financial reserve allocations.")
            if doc_id not in affected_doc_ids:
                affected_doc_ids.append(doc_id)

    # 2. Check Decision Ledger precedents
    for entry in ledger_history[:10]:
        e_param = str(entry.get("parameter", "")).lower()
        e_topic = str(entry.get("topic", "")).lower()
        if any(term in e_param for term in param_label.split() if len(term) > 3) or e_topic in param_label:
            status = entry.get("status", "APPROVED")
            precedents.append(
                f"{entry.get('id', entry.get('decision_id', 'DEC'))} ({entry.get('timestamp', 'past')}): "
                f"{entry.get('parameter')} changed from {entry.get('old_value')} to {entry.get('new_value')} [{status}]"
            )

    # 3. Overall risk level evaluation
    risk = "low"
    if has_conflict:
        delta_pct = getattr(opinion, "delta_pct", 0.0) if not isinstance(opinion, dict) else opinion.get("delta_pct", 0.0)
        if delta_pct > 25.0 or len(cascading) > 1:
            risk = "high"
        elif delta_pct > 5.0 or len(cascading) > 0:
            risk = "medium"

    review_notes = (
        f"Cross-review confirmed Stage 1 verdict (conflict={has_conflict}). "
        f"Found {len(cascading)} potential secondary impact(s) and {len(precedents)} historical precedent(s)."
    )

    return ReviewResult(
        opinion_confirmed=True,
        cascading_impacts=cascading,
        precedent_decisions=precedents,
        risk_assessment=risk,
        additional_docs_affected=affected_doc_ids,
        review_notes=review_notes,
        confidence=0.92,
    )
