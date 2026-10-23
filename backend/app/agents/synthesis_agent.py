"""
Parishad Council of Agents — Stage 3: Synthesis Agent.

Synthesizes the First Opinion and Cross-Review into a definitive recommendation:
1. Final verdict (CONFLICT_DETECTED | ALIGNED | NEEDS_DISCUSSION)
2. Plain-English executive summary for non-technical managers
3. Draft of the proposed updated official document text
4. Actionable review checklist for human approvers
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


class SynthesisResult(BaseModel):
    """Final consensus recommendation from the Council of Agents."""
    final_verdict: str = Field(..., description="CONFLICT_DETECTED | ALIGNED | NEEDS_DISCUSSION")
    confidence: float = Field(..., ge=0.0, le=1.0)
    human_summary: str = Field(..., description="Executive summary of the change.")
    proposed_doc_update: str = Field(..., description="Proposed updated official document content.")
    risk_level: str = Field("low", description="low | medium | high | critical")
    recommended_action: str = Field("STAGE_APPROVAL", description="STAGE_APPROVAL | AUTO_PASS | REJECT")
    review_checklist: list[str] = Field(default_factory=list, description="Actionable verification items.")


async def _generate_ai_summary(
    human_summary: str,
    original_text: str,
    proposed_update: str,
) -> str | None:
    """Optional call to Gemini to generate manager-friendly summary."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        return None

    prompt = (
        "You are the Lead Synthesizer for Parishad Council of Agents. "
        "Summarize this change for an engineering executive in 2 crisp sentences:\n\n"
        f"Original Context: {original_text}\n"
        f"Proposed Change: {proposed_update}\n"
        f"Technical Findings: {human_summary}\n"
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
        logger.warning("Gemini synthesis call failed: %s", exc)
    return None


async def synthesize_recommendation(
    opinion: Any,
    review: Any,
    original_doc: dict[str, Any] | None,
    update_text: str,
) -> SynthesisResult:
    """
    Stage 3: Generate the final consensus verdict and proposed document wording.
    """
    has_conflict = getattr(opinion, "has_conflict", False) if not isinstance(opinion, dict) else opinion.get("has_conflict", False)
    param_label = getattr(opinion, "parameter_label", "Rule Parameter") if not isinstance(opinion, dict) else opinion.get("parameter_label", "Rule Parameter")
    official_val = getattr(opinion, "official_value", "") if not isinstance(opinion, dict) else opinion.get("official_value", "")
    proposed_val = getattr(opinion, "proposed_value", "") if not isinstance(opinion, dict) else opinion.get("proposed_value", "")
    delta_pct = getattr(opinion, "delta_pct", 0.0) if not isinstance(opinion, dict) else opinion.get("delta_pct", 0.0)
    risk = getattr(review, "risk_assessment", "low") if not isinstance(review, dict) else review.get("risk_assessment", "low")
    cascading = getattr(review, "cascading_impacts", []) if not isinstance(review, dict) else review.get("cascading_impacts", [])

    doc_content = original_doc.get("content", "") if original_doc else ""
    doc_title = original_doc.get("title", "Official Specification") if original_doc else "Official Specification"
    doc_id = original_doc.get("doc_id", "DOC") if original_doc else "DOC"

    # Parameter-aware proposed updated document text (avoids naive global number substitution)
    proposed_doc_text = doc_content
    if official_val and proposed_val:
        m_old = re.search(r"(\d+(?:\.\d+)?)", official_val)
        m_new = re.search(r"(\d+(?:\.\d+)?)", proposed_val)
        if m_old and m_new:
            old_num_str = m_old.group(1)
            new_num_str = m_new.group(1)
            param_keywords = [w.lower() for w in param_label.split() if len(w) > 3]

            # Split sentences and find the sentence specifically referencing this parameter
            sentences = re.split(r"(?<=[.!?])\s+", doc_content)
            replaced_in_sentence = False
            updated_sentences = []

            for s in sentences:
                s_lower = s.lower()
                # Check if this sentence mentions the parameter keywords AND contains the old number
                if not replaced_in_sentence and old_num_str in s and any(kw in s_lower for kw in param_keywords):
                    # Replace only inside this targeted parameter sentence
                    s_updated = s.replace(old_num_str, new_num_str, 1)
                    updated_sentences.append(s_updated)
                    replaced_in_sentence = True
                else:
                    updated_sentences.append(s)

            if replaced_in_sentence:
                proposed_doc_text = " ".join(updated_sentences)
            elif old_num_str in doc_content:
                # If no specific sentence matched keywords, replace old_num_str specifically
                proposed_doc_text = doc_content.replace(old_num_str, new_num_str, 1)
            else:
                proposed_doc_text = f"{doc_content}\n\n[Proposed Update: {param_label} changed from {official_val} to {proposed_val}]"

    checklist: list[str] = []
    if has_conflict:
        final_verdict = "CONFLICT_DETECTED"
        recommended_action = "STAGE_APPROVAL"
        checklist.append(f"Verify hardware or policy tolerance for {param_label} change ({official_val} → {proposed_val}).")
        if cascading:
            checklist.append(f"Audit {len(cascading)} secondary system impact(s) flagged by Cross-Review.")
        checklist.append(f"Require human sign-off before committing to {doc_id}.")

        base_summary = (
            f"Update proposes changing {param_label} from certified {official_val} to {proposed_val} "
            f"({delta_pct}% difference) against '{doc_title}'. Parishad flagged this change and prepared "
            f"an approval draft for human sign-off."
        )
        ai_summary = await _generate_ai_summary(base_summary, doc_content, proposed_doc_text)
        human_summary = ai_summary or base_summary
        confidence = 0.95
    else:
        final_verdict = "ALIGNED"
        recommended_action = "AUTO_PASS"
        human_summary = f"Update is fully compliant with '{doc_title}' ({doc_id}). No rule conflicts detected."
        confidence = 0.96

    return SynthesisResult(
        final_verdict=final_verdict,
        confidence=confidence,
        human_summary=human_summary,
        proposed_doc_update=proposed_doc_text,
        risk_level=risk,
        recommended_action=recommended_action,
        review_checklist=checklist,
    )
