"""
Parishad Council of Agents — Stage 1: First Opinion Agent.

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
        f"You are an expert technical auditor in the Parishad Council of Agents.\n"
        f"Official Document: {doc_title}\n"
        f"Document Content: {doc_content}\n"
        f"Official Parameter Value: {official_val}\n"
        f"Proposed Parameter Value: {new_val}\n"
        f"Incoming Message: {update_text}\n\n"
        f"Analyze the technical and business impact of this change in 2 clear sentences. "
        f"State whether this introduces risk or requires team review."
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
            )
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
    prop_unit_norm = str(prop_unit).lower().strip()
    update_text_lower = update_text.lower()

    # Parameter-aware matching:
    # 1. Exact unit match
    unit_matched_rules = [
        r for r in doc_rules
        if r.get("unit", "").lower().strip() == prop_unit_norm
    ]

    if len(unit_matched_rules) == 1:
        matching_rule = unit_matched_rules[0]
    elif len(unit_matched_rules) > 1:
        # Disambiguate by parameter label keywords matching update text
        best_r = None
        best_score = -1
        for r in unit_matched_rules:
            lbl = r.get("label", "").lower()
            score = sum(1 for word in lbl.split() if len(word) > 3 and word in update_text_lower)
            if score > best_score:
                best_score = score
                best_r = r
        matching_rule = best_r or unit_matched_rules[0]
    else:
        # Fallback ONLY if parameter name matches explicitly in rule label
        for r in doc_rules:
            lbl = r.get("label", "").lower()
            if any(term in lbl for term in ["voltage", "cutoff", "speed", "refund", "thermal", "latency", "frequency"] if term in update_text_lower):
                # Verify dimension compatibility
                r_unit = r.get("unit", "").lower()
                if (prop_unit_norm in ["v", "volts"] and r_unit in ["v", "volts"]) or \
                   (prop_unit_norm in ["mbps", "kbps"] and r_unit in ["mbps", "kbps"]) or \
                   (prop_unit_norm in ["days", "hours"] and r_unit in ["days", "hours"]):
                    matching_rule = r
                    break

    # Strictly DO NOT fall back to doc_rules[0] when no parameter corresponds!
    if not matching_rule or prop_val is None:
        return OpinionResult(
            has_conflict=False,
            severity="low",
            parameter_label=prop_label,
            proposed_value=f"{prop_val} {prop_display_unit}" if prop_val is not None else "",
            reasoning=f"INSUFFICIENT_EVIDENCE: No matching parameter or rule constraint found for '{prop_label}' in official document '{matched_doc.get('title')}'.",
            confidence=0.75,
        )

    official_num = float(matching_rule.get("value", 0.0))
    proposed_num = float(prop_val)
    rule_label = matching_rule.get("label", prop_label)
    rule_unit = matching_rule.get("unit", prop_display_unit)

    official_formatted = f"{official_num} {rule_unit.upper()}"
    proposed_formatted = f"{proposed_num} {rule_unit.upper()}"

    if official_num != proposed_num:
        # Deterministic percentage formula with explicit zero-baseline handling
        if official_num != 0.0:
            delta = abs((proposed_num - official_num) / abs(official_num)) * 100.0
            delta_pct = round(delta, 1)
            is_zero_baseline = False
        else:
            delta_pct = 100.0
            is_zero_baseline = True

        severity = "low"
        if delta_pct > 50.0 or is_zero_baseline:
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

        if is_zero_baseline:
            base_reason = (
                f"Special Case: Official baseline for {rule_label} is 0 {rule_unit}. "
                f"Proposed value introduces a non-zero threshold of {proposed_formatted}, requiring human review."
            )
            reasoning = f"{base_reason} {ai_reason}" if ai_reason else base_reason
        else:
            base_reason = (
                f"Update changes {rule_label} from official {official_formatted} to {proposed_formatted} "
                f"({delta_pct}% shift). This exceeds certified tolerances in {matched_doc.get('doc_id')}."
            )
            reasoning = ai_reason or base_reason

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
