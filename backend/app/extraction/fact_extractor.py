"""
CortexOS — Universal Fact & Numeric Value Extractor.

Parses arbitrary domain text and extracts every numeric value along with its
unit, producing structured ``ExtractedFact`` objects.  Also detects *change
patterns* such as "from 120V to 240V" so the pipeline can record old → new
transitions.

Supported unit families:
  Electrical  — V, volts, A, amps
  Networking  — Mbps, kbps, Gbps
  Time        — days, hours, minutes, seconds, ms
  Temperature — °C, C, °F, F
  Percentage  — %
  Storage     — GB, MB, TB, KB
  Currency    — USD, $  (with comma-separated thousands)
  Frequency   — Hz, kHz, MHz, GHz
  Distance    — km, m, mm, cm, mi
  Mass        — kg, g, lb, lbs
  Mechanical  — RPM

Usage::

    from app.extraction.fact_extractor import extract_facts

    result = extract_facts("Latency improved from 120 ms to 45 ms")
    print(result.change_detected)  # True
    print(result.old_fact.value)   # 120.0
    print(result.new_fact.value)   # 45.0
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field


# ── Data Models ─────────────────────────────────────────────────────────────

class ExtractedFact(BaseModel):
    """A single numeric fact extracted from text with span and provenance."""
    value: float = Field(..., description="Parsed numeric value.")
    unit: str = Field(..., description="Normalized canonical unit (e.g. 'V', 'Mbps').")
    display_unit: str = Field(..., description="Unit as it originally appeared in the text.")
    label: str = Field("", description="Auto-generated human-readable label for the fact.")
    start_char: int = Field(-1, description="Start index in original source text.")
    end_char: int = Field(-1, description="End index in original source text.")
    source_text: str = Field("", description="Exact matching substring.")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Extraction confidence score.")


class ExtractionResult(BaseModel):
    """Aggregated extraction output for a text passage."""
    has_signal: bool = Field(False, description="True when at least one numeric fact was found.")
    all_facts: list[ExtractedFact] = Field(
        default_factory=list,
        description="Every numeric fact found, in document order.",
    )
    proposed_fact: Optional[ExtractedFact] = Field(
        None,
        description="The resolved candidate/target fact proposed for update.",
    )
    change_detected: bool = Field(
        False,
        description="True when a 'from X to Y' transition was detected.",
    )
    old_fact: Optional[ExtractedFact] = Field(
        None, description="The *old* value in a detected change pattern.",
    )
    new_fact: Optional[ExtractedFact] = Field(
        None, description="The *new* value in a detected change pattern.",
    )
    is_ambiguous: bool = Field(
        False,
        description="True if multiple conflicting candidate parameters exist without clear target intent.",
    )
    ambiguity_reason: Optional[str] = Field(
        None,
        description="Diagnostic explanation when candidate proposal resolution is ambiguous.",
    )


# ── Unit Normalisation Map ──────────────────────────────────────────────────
# Maps every recognised surface form (lowercased) → canonical unit string.

_UNIT_NORM: dict[str, str] = {
    # Electrical
    "v": "V", "volt": "V", "volts": "V",
    "a": "A", "amp": "A", "amps": "A", "ampere": "A", "amperes": "A",
    # Networking
    "mbps": "Mbps", "kbps": "kbps", "gbps": "Gbps", "bps": "bps",
    # Time
    "day": "days", "days": "days",
    "hour": "hours", "hours": "hours", "hr": "hours", "hrs": "hours",
    "minute": "minutes", "minutes": "minutes", "min": "minutes", "mins": "minutes",
    "second": "seconds", "seconds": "seconds", "sec": "seconds", "secs": "seconds", "s": "seconds",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    # Temperature
    "°c": "°C", "c": "°C", "celsius": "°C",
    "°f": "°F", "f": "°F", "fahrenheit": "°F",
    # Percentage & Counts
    "%": "%", "percent": "%", "pct": "%",
    "defects": "defects", "defect": "defects",
    # Storage
    "gb": "GB", "mb": "MB", "tb": "TB", "kb": "KB", "pb": "PB",
    # Currency
    "usd": "USD", "$": "USD", "dollars": "USD", "dollar": "USD",
    # Frequency
    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz",
    # Distance
    "km": "km", "m": "m", "mm": "mm", "cm": "cm", "mi": "mi", "mile": "mi", "miles": "mi",
    # Mass
    "kg": "kg", "g": "g", "lb": "lb", "lbs": "lb",
    # Mechanical
    "rpm": "RPM",
}

# Build a regex alternation of all known unit surface forms, longest first
# to avoid partial matches (e.g. "GHz" before "Hz").
_UNIT_ALTS = sorted(_UNIT_NORM.keys(), key=len, reverse=True)
_UNIT_RE = "|".join(re.escape(u) for u in _UNIT_ALTS)

# ── Core Extraction Regex ───────────────────────────────────────────────────
# Matches: optional $ prefix, number (with optional commas and decimals), optional unit.
_NUMBER_UNIT_RE = re.compile(
    r"(\$\s*)?"                          # optional dollar sign
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"  # number with optional commas
    r"\s*"
    r"(" + _UNIT_RE + r")?"             # optional unit
    r"(?=[\s,;.\)\]\}:!?\"\'—–\-]|$)",  # lookahead: boundary
    re.IGNORECASE,
)

# "from X to Y" change pattern — captures the two number+unit groups.
_CHANGE_RE = re.compile(
    r"from\s+"
    r"(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(" + _UNIT_RE + r")?"
    r"\s+to\s+"
    r"(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(" + _UNIT_RE + r")?",
    re.IGNORECASE,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_number(raw: str) -> float:
    """Convert a raw number string (possibly with ``$`` prefix or commas) to float."""
    cleaned = raw.replace("$", "").replace(",", "").strip()
    return float(cleaned)


def _normalize_unit(raw: str | None, has_dollar: bool = False) -> tuple[str, str]:
    """Return ``(canonical_unit, display_unit)`` for a raw unit string."""
    if has_dollar and not raw:
        return "USD", "$"
    if not raw:
        return "", ""
    display = raw.strip()
    canonical = _UNIT_NORM.get(display.lower(), display)
    return canonical, display


def _make_label(value: float, unit: str) -> str:
    """Generate a concise human-readable label."""
    # Format large numbers without unnecessary decimals.
    if value == int(value):
        num_str = f"{int(value):,}"
    else:
        num_str = f"{value:,.2f}"
    if unit:
        return f"{num_str} {unit}"
    return num_str


# ── Public API ──────────────────────────────────────────────────────────────

def extract_facts(text: str) -> ExtractionResult:
    """Extract all numeric facts and detect value changes in *text*.

    Parameters
    ----------
    text:
        Free-form text potentially containing numeric values with units.

    Returns
    -------
    ExtractionResult
        Structured extraction with every fact, the proposed (last) fact,
        and optional old/new facts when a change pattern is found.
    """
    if not text or not text.strip():
        return ExtractionResult()

    all_facts: list[ExtractedFact] = []

    for m in _NUMBER_UNIT_RE.finditer(text):
        dollar_prefix = m.group(1)
        raw_num = m.group(2)
        raw_unit = m.group(3)
        has_dollar = bool(dollar_prefix and "$" in dollar_prefix)

        value = _parse_number(raw_num)
        canonical, display = _normalize_unit(raw_unit, has_dollar=has_dollar)

        fact = ExtractedFact(
            value=value,
            unit=canonical,
            display_unit=display if display else ("$" if has_dollar else ""),
            label=_make_label(value, canonical),
            start_char=m.start(),
            end_char=m.end(),
            source_text=m.group(0),
            confidence=0.98 if canonical else 0.75,
        )
        all_facts.append(fact)

    # ── Change detection ────────────────────────────────────────────────
    change_detected = False
    old_fact: ExtractedFact | None = None
    new_fact: ExtractedFact | None = None

    change_match = _CHANGE_RE.search(text)
    if change_match:
        old_raw = change_match.group(1)
        old_unit_raw = change_match.group(2)
        new_raw = change_match.group(3)
        new_unit_raw = change_match.group(4)

        old_has_dollar = "$" in old_raw
        new_has_dollar = "$" in new_raw

        old_val = _parse_number(old_raw)
        new_val = _parse_number(new_raw)

        old_canon, old_disp = _normalize_unit(old_unit_raw, has_dollar=old_has_dollar)
        new_canon, new_disp = _normalize_unit(new_unit_raw, has_dollar=new_has_dollar)

        # If new side has no unit, inherit from old side.
        if not new_canon and old_canon:
            new_canon, new_disp = old_canon, old_disp

        old_fact = ExtractedFact(
            value=old_val,
            unit=old_canon,
            display_unit=old_disp if old_disp else ("$" if old_has_dollar else ""),
            label=_make_label(old_val, old_canon),
            start_char=change_match.start(),
            end_char=change_match.start() + len(old_raw) + (len(old_unit_raw) if old_unit_raw else 0),
            source_text=old_raw,
            confidence=0.99,
        )
        new_fact = ExtractedFact(
            value=new_val,
            unit=new_canon,
            display_unit=new_disp if new_disp else ("$" if new_has_dollar else ""),
            label=_make_label(new_val, new_canon),
            start_char=change_match.end() - len(new_raw) - (len(new_unit_raw) if new_unit_raw else 0),
            end_char=change_match.end(),
            source_text=new_raw,
            confidence=0.99,
        )
        change_detected = True

    # ── Intelligent Proposal Resolution (Not blindly last number) ────────
    proposed: ExtractedFact | None = None
    is_ambiguous = False
    ambiguity_reason = None

    if change_detected and new_fact:
        proposed = new_fact
    elif all_facts:
        # Check for explicit proposal action keywords (e.g., 'to 54V', 'set to 42.5V', 'update to 60V')
        proposal_kw_re = re.compile(
            r"(?:to|set|update|change|raise|lower|increase|decrease|bump|target|limit|threshold)\s+([a-zA-Z\s]{0,15})?\$?(\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        kw_matches = list(proposal_kw_re.finditer(text))

        candidate = None
        if kw_matches:
            # Match fact nearest to the last proposal keyword
            last_kw = kw_matches[-1]
            kw_pos = last_kw.start()
            candidate_facts = [f for f in all_facts if f.start_char >= kw_pos]
            if candidate_facts:
                candidate = candidate_facts[0]

        if candidate:
            proposed = candidate
        else:
            # Filter facts: prefer facts that have explicit units over unitless facts
            facts_with_units = [f for f in all_facts if f.unit]
            if len(facts_with_units) == 1:
                proposed = facts_with_units[0]
            elif len(facts_with_units) > 1:
                # Multiple facts with units: if units differ, check if one matches proposal pattern
                distinct_units = set(f.unit for f in facts_with_units)
                if len(distinct_units) > 1:
                    proposed = facts_with_units[-1]
                    is_ambiguous = True
                    ambiguity_reason = f"Multiple distinct unit parameters detected ({', '.join(distinct_units)}). Selected {proposed.label} as primary candidate."
                else:
                    proposed = facts_with_units[-1]
            else:
                proposed = all_facts[-1]

    return ExtractionResult(
        has_signal=len(all_facts) > 0,
        all_facts=all_facts,
        proposed_fact=proposed,
        change_detected=change_detected,
        old_fact=old_fact,
        new_fact=new_fact,
        is_ambiguous=is_ambiguous,
        ambiguity_reason=ambiguity_reason,
    )
