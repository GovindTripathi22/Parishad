"""
CortexOS — Signal-vs-Noise Filter.

Classifies incoming text into actionable signal categories or rejects it as
noise.  When a ``GEMINI_API_KEY`` is configured the module calls the Gemini
generative AI API for high-accuracy classification; otherwise it falls back
to a keyword-based heuristic engine that requires no external services.

Signal categories:
  • ``decision``     — Authoritative choices, approvals, rejections
  • ``sop``          — Standard operating procedures, runbooks, playbooks
  • ``architecture`` — System design, topology, infrastructure changes
  • ``policy``       — Organizational rules, compliance, governance
  • ``incident``     — Outages, post-mortems, alerts, degradations
  • ``noise``        — Chatter, pleasantries, irrelevant content

Usage::

    from app.extraction.signal_filter import classify_signal

    result = await classify_signal(
        "We decided to migrate the auth service to mTLS by Q3.",
        source="slack",
    )
    print(result.signal_type)  # "decision"
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)


# ── Data Models ─────────────────────────────────────────────────────────────

class SignalResult(BaseModel):
    """Classification result for a piece of incoming text."""
    is_signal: bool = Field(
        ..., description="True when the text is classified as actionable (not noise).",
    )
    signal_type: Optional[str] = Field(
        None,
        description="One of: decision, sop, architecture, policy, incident, or None for noise.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Classifier confidence (0 = no confidence, 1 = certain).",
    )
    reasoning: str = Field(
        "", description="Human-readable explanation of the classification.",
    )


# ── Valid signal types ──────────────────────────────────────────────────────

_VALID_SIGNAL_TYPES = {"decision", "sop", "architecture", "policy", "incident"}


# ── Keyword Heuristic Engine ───────────────────────────────────────────────

# Each entry: (signal_type, keyword list, base weight per keyword hit)
_KEYWORD_RULES: list[tuple[str, list[str], float]] = [
    (
        "decision",
        [
            "decided", "approved", "rejected", "declined", "changed", "updated",
            "merged", "ratified", "resolved", "adopted", "committed", "agreed",
            "finalized", "concluded", "voted", "authorized", "signed off",
            "green-lit", "go/no-go", "ship it",
        ],
        0.18,
    ),
    (
        "sop",
        [
            "procedure", "runbook", "playbook", "step-by-step", "checklist",
            "standard operating", "how to", "instructions", "guidelines",
            "workflow", "protocol", "manual",
        ],
        0.20,
    ),
    (
        "architecture",
        [
            "architecture", "system design", "topology", "microservice",
            "infrastructure", "deployment", "kubernetes", "docker", "terraform",
            "load balancer", "database migration", "schema change", "api gateway",
            "service mesh", "cdn", "cluster", "failover", "replica",
        ],
        0.16,
    ),
    (
        "policy",
        [
            "policy", "compliance", "governance", "regulation", "mandate",
            "requirement", "audit", "sox", "gdpr", "hipaa", "iso 27001",
            "security policy", "access control", "retention",
        ],
        0.18,
    ),
    (
        "incident",
        [
            "incident", "outage", "downtime", "post-mortem", "postmortem",
            "degradation", "alert", "pager", "sev1", "sev2", "p0", "p1",
            "root cause", "rca", "rollback", "hotfix", "emergency",
        ],
        0.20,
    ),
]

# Unit-bearing numbers boost signal confidence (suggests quantitative data).
_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:V|volts?|A|amps?|Mbps|kbps|Gbps|ms|seconds?|hours?|days?|"
    r"°[CF]|%|GB|MB|TB|USD|\$|Hz|kHz|MHz|GHz|km|m|kg|RPM)",
    re.IGNORECASE,
)


def _heuristic_classify(text: str) -> SignalResult:
    """Classify *text* using keyword heuristics (no LLM call)."""
    lower = text.lower()

    scores: dict[str, float] = {}
    match_details: dict[str, list[str]] = {}

    for signal_type, keywords, weight in _KEYWORD_RULES:
        matched_kws: list[str] = []
        for kw in keywords:
            if kw in lower:
                matched_kws.append(kw)
        if matched_kws:
            # Diminishing returns: each additional keyword adds less.
            score = weight * (1.0 + 0.3 * (len(matched_kws) - 1))
            scores[signal_type] = score
            match_details[signal_type] = matched_kws

    # Bonus for unit-bearing numbers.
    unit_matches = _UNIT_RE.findall(text)
    if unit_matches:
        for st in scores:
            scores[st] += 0.08 * min(len(unit_matches), 3)

    if not scores:
        return SignalResult(
            is_signal=False,
            signal_type=None,
            confidence=0.65,
            reasoning="No decision, SOP, architecture, policy, or incident keywords detected.",
        )

    best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
    raw_conf = min(scores[best_type], 1.0)
    # Heuristic caps at 0.85 to reflect inherent uncertainty.
    confidence = round(min(raw_conf, 0.85), 4)

    matched_keywords = match_details.get(best_type, [])
    reasoning = (
        f"Keyword heuristic matched '{best_type}' signals: "
        f"{', '.join(matched_keywords)}."
    )
    if unit_matches:
        reasoning += f" Also found {len(unit_matches)} numeric value(s) with units."

    return SignalResult(
        is_signal=True,
        signal_type=best_type,
        confidence=confidence,
        reasoning=reasoning,
    )


# ── Gemini API Classification ──────────────────────────────────────────────

_GEMINI_SYSTEM_PROMPT = """\
You are a text classifier for a Company Brain system.  Classify the following
text into EXACTLY ONE of these categories:

  decision     — authoritative choices, approvals, rejections, go/no-go
  sop          — standard operating procedures, runbooks, step-by-step guides
  architecture — system design, infrastructure, deployment topology
  policy       — organizational rules, compliance, governance mandates
  incident     — outages, post-mortems, alerts, degradations
  noise        — chatter, pleasantries, off-topic, irrelevant

Respond with a JSON object (no markdown, no extra text):
{
  "signal_type": "<category>",
  "confidence": <float 0-1>,
  "reasoning": "<one sentence>"
}
"""


async def _gemini_classify(text: str) -> SignalResult | None:
    """Attempt classification via the Gemini generative AI API.

    Returns ``None`` on any failure so the caller can fall back to heuristics.
    """
    settings = get_settings()
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL

    if not api_key:
        return None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{_GEMINI_SYSTEM_PROMPT}\n\n---\nTEXT:\n{text}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 256,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()

        body = resp.json()
        raw_text = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )

        # Strip markdown fences if present.
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)

        data = json.loads(cleaned)
        signal_type = data.get("signal_type", "noise").lower().strip()
        confidence = float(data.get("confidence", 0.5))
        reasoning = data.get("reasoning", "Classified by Gemini.")

        is_signal = signal_type in _VALID_SIGNAL_TYPES

        return SignalResult(
            is_signal=is_signal,
            signal_type=signal_type if is_signal else None,
            confidence=round(min(max(confidence, 0.0), 1.0), 4),
            reasoning=f"[Gemini] {reasoning}",
        )

    except httpx.HTTPStatusError as exc:
        logger.warning("Gemini API HTTP error %s: %s", exc.response.status_code, exc)
        return None
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
        logger.warning("Failed to parse Gemini response: %s", exc)
        return None
    except httpx.RequestError as exc:
        logger.warning("Gemini API request error: %s", exc)
        return None


# ── Public API ──────────────────────────────────────────────────────────────

async def classify_signal(
    text: str,
    source: str = "unknown",
) -> SignalResult:
    """Classify *text* as a signal or noise.

    Tries the Gemini API first; falls back to keyword heuristics when the API
    key is not set or the call fails.

    Parameters
    ----------
    text:
        The raw text to classify.
    source:
        Origin channel (e.g. ``"slack"``, ``"github"``, ``"jira"``).
        Currently used for logging; future versions may weight sources.

    Returns
    -------
    SignalResult
        Classification verdict with type, confidence, and reasoning.
    """
    if not text or not text.strip():
        return SignalResult(
            is_signal=False,
            signal_type=None,
            confidence=1.0,
            reasoning="Empty input is always noise.",
        )

    # Try LLM first.
    gemini_result = await _gemini_classify(text)
    if gemini_result is not None:
        logger.debug(
            "Gemini classified text from '%s' as %s (%.2f)",
            source,
            gemini_result.signal_type,
            gemini_result.confidence,
        )
        return gemini_result

    # Fallback to keyword heuristics.
    result = _heuristic_classify(text)
    logger.debug(
        "Heuristic classified text from '%s' as %s (%.2f)",
        source,
        result.signal_type,
        result.confidence,
    )
    return result
