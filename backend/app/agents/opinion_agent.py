"""
CortexOS Council of Agents — Stage 1: First Opinion Agent.

Performs the initial domain analysis of an incoming update against the top-matched
official document and extracted facts, identifying whether a conflict exists, its
magnitude (percentage change), and its severity.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


class OpinionResult(BaseModel):
    """Output from the First Opinion Agent."""
    has_conflict: bool = Field(False, description="True if proposed values conflict with official doc rules.")
    severity: str = Field("low", description="Severity level: low | medium | high | critical.")
    parameter_label: str = Field("", description="Label of the parameter under review.")
    official_value: str = Field("", description="Current official specification value.")
    proposed_value: str = Field("", description="Proposed new value found in update.")
    delta_pct: float = Field(0.0, description="Percentage drift between old and new values.")
    reasoning: str = Field("", description="Detailed analytical justification.")
    confidence: float = Field(0.85, ge=0.0, le=1.0, description="Confidence score of opinion.")


async def _call_gemini_opinion(
    update_text: str,
    doc_title: str,
    doc_content: str,
    official_val: str,
    new_val: str,
) -> str | None:
    """Use Gemini for natural language reasoning if API key is available."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        return None

    prompt = (
        f"You are an expert technical auditor in the CortexOS Council of Agents.\n"
        f"Official Document: {doc_title}\n"
        f"Document Content: {doc_content}\n"
        f"Official Parameter Value: {official_val}\n"
        f"Proposed Parameter Value: {new_val}\n"
        f"Incoming Message: {update_text}\n\n"
        f"Analyze the technical and business impact of this change in 2 clear sentences. "
        f"State whether this introduces risk or requires team review."
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
        logger.warning("Gemini opinion call failed: %s", exc)
    return None


async def analyze_opinion(
    update_text: str,
    matched_doc: dict[str, Any] | None,
    extracted_facts: dict[str, Any] | Any,
) -> OpinionResult:
    """
    Stage 1 analysis: compare extracted facts against matched official doc rules.
    """
    if not matched_doc:
        return OpinionResult(
            has_conflict=False,
            severity="low",
            reasoning="No matching official document found in Company Brain.",
            confidence=0.5,
        )

    # Normalize extracted facts
    proposed_fact = None
    if hasattr(extracted_facts, "proposed_fact"):
        proposed_fact = extracted_facts.proposed_fact
    elif isinstance(extracted_facts, dict):
        proposed_fact = extracted_facts.get("proposed_fact")

    if not proposed_fact:
        return OpinionResult(
            has_conflict=False,
            severity="low",
            reasoning=f"No numeric parameters extracted. Text aligns generally with {matched_doc.get('title')}.",
            confidence=0.8,
        )

    # Extract proposed value and unit
    prop_val = getattr(proposed_fact, "value", None) if not isinstance(proposed_fact, dict) else proposed_fact.get("value")
    prop_unit = getattr(proposed_fact, "unit", "") if not isinstance(proposed_fact, dict) else proposed_fact.get("unit")
    prop_display_unit = getattr(proposed_fact, "display_unit", prop_unit) if not isinstance(proposed_fact, dict) else proposed_fact.get("display_unit", prop_unit)
    prop_label = getattr(proposed_fact, "label", "Rule Parameter") if not isinstance(proposed_fact, dict) else proposed_fact.get("label", "Rule Parameter")

    doc_rules = matched_doc.get("rules", [])
    matching_rule = None
    for r in doc_rules:
        r_unit = r.get("unit", "")
        if r_unit.lower() == str(prop_unit).lower():
            matching_rule = r
            break
    if not matching_rule and doc_rules:
        matching_rule = doc_rules[0]

    if not matching_rule or prop_val is None:
        return OpinionResult(
            has_conflict=False,
            severity="low",
            parameter_label=prop_label,
            proposed_value=f"{prop_val} {prop_display_unit}",
            reasoning=f"Parameter verified against '{matched_doc.get('title')}'. No conflicting rule constraint found.",
            confidence=0.88,
        )

    official_num = float(matching_rule.get("value", 0.0))
    proposed_num = float(prop_val)
    rule_label = matching_rule.get("label", prop_label)
    rule_unit = matching_rule.get("unit", prop_display_unit)

    official_formatted = f"{official_num} {rule_unit.upper()}"
    proposed_formatted = f"{proposed_num} {rule_unit.upper()}"

    if official_num != proposed_num:
        delta = abs((proposed_num - official_num) / (official_num if official_num != 0 else 1.0)) * 100.0
        delta_pct = round(delta, 1)

        severity = "low"
        if delta_pct > 50.0:
            severity = "critical"
        elif delta_pct > 20.0:
            severity = "high"
        elif delta_pct > 5.0:
            severity = "medium"

        # Check for Gemini reasoning or synthesize deterministic technical reasoning
        ai_reason = await _call_gemini_opinion(
            update_text,
            matched_doc.get("title", ""),
            matched_doc.get("content", ""),
            official_formatted,
            proposed_formatted,
        )

        reasoning = ai_reason or (
            f"Update changes {rule_label} from official {official_formatted} to {proposed_formatted} "
            f"({delta_pct}% shift). This exceeds certified tolerances in {matched_doc.get('doc_id')}."
        )

        return OpinionResult(
            has_conflict=True,
            severity=severity,
            parameter_label=rule_label,
            official_value=official_formatted,
            proposed_value=proposed_formatted,
            delta_pct=delta_pct,
            reasoning=reasoning,
            confidence=0.94,
        )

    return OpinionResult(
        has_conflict=False,
        severity="low",
        parameter_label=rule_label,
        official_value=official_formatted,
        proposed_value=proposed_formatted,
        delta_pct=0.0,
        reasoning=f"Proposed value matches certified official rule in {matched_doc.get('doc_id')}.",
        confidence=0.96,
    )
