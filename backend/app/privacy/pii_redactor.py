"""
CortexOS — PII Detection & Redaction Module.

Detects and redacts personally identifiable information (PII) and sensitive
secrets from arbitrary text before it enters the Company Brain pipeline.

Supported pattern categories:
  • Email addresses
  • Phone numbers (US / international)
  • Social Security Numbers (SSN)
  • Credit / debit card numbers (Visa, MC, Amex, Discover)
  • API keys (AWS AKIA*, OpenAI sk-*, generic long hex secrets)
  • IPv4 and IPv6 addresses

Usage::

    from app.privacy.pii_redactor import redact

    result = redact("Contact john@acme.com or 555-123-4567")
    print(result.clean_text)       # "Contact [EMAIL_REDACTED] or [PHONE_REDACTED]"
    print(result.redacted_count)   # 2
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


# ── Data Models ─────────────────────────────────────────────────────────────

class RedactedItem(BaseModel):
    """A single redacted PII occurrence."""
    type: str = Field(..., description="Category of PII detected (e.g. 'email', 'ssn').")
    original: str = Field(..., description="The original matched text.")
    replacement: str = Field(..., description="The placeholder that replaced it.")


class RedactionResult(BaseModel):
    """Aggregated result of a redaction pass."""
    clean_text: str = Field(..., description="Text after all PII has been replaced.")
    redacted_items: list[RedactedItem] = Field(
        default_factory=list,
        description="Ordered list of every individual redaction performed.",
    )
    redacted_count: int = Field(0, description="Total number of redactions performed.")
    is_clean: bool = Field(True, description="True when no PII was detected.")


# ── Pattern Registry ────────────────────────────────────────────────────────

class _PIIPattern(BaseModel):
    """Defines a single regex-based PII detection rule."""
    name: str
    pattern: re.Pattern[str]
    replacement: str
    priority: int = 0  # lower = matched first

    model_config = {"arbitrary_types_allowed": True}


# Singleton, mutable registry so callers can extend at runtime.
_PATTERN_REGISTRY: list[_PIIPattern] = []


def register_pattern(
    name: str,
    pattern: str | re.Pattern[str],
    replacement: str,
    *,
    priority: int = 100,
    flags: re.RegexFlag = re.IGNORECASE,
) -> None:
    """Register a new PII detection pattern.

    Parameters
    ----------
    name:
        Human-readable category (e.g. ``"passport"``).
    pattern:
        Regex string or compiled pattern.
    replacement:
        Placeholder text inserted in place of each match.
    priority:
        Lower values are evaluated first.  Built-in patterns use 0-50.
    flags:
        Regex flags applied when *pattern* is a ``str``.
    """
    compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, flags)
    _PATTERN_REGISTRY.append(
        _PIIPattern(name=name, pattern=compiled, replacement=replacement, priority=priority)
    )
    # Keep sorted by priority so deterministic evaluation order is guaranteed.
    _PATTERN_REGISTRY.sort(key=lambda p: p.priority)


def clear_registry() -> None:
    """Remove **all** registered patterns (including built-ins)."""
    _PATTERN_REGISTRY.clear()


def list_patterns() -> list[dict[str, Any]]:
    """Return a snapshot of every registered pattern (for debugging / admin UIs)."""
    return [
        {"name": p.name, "replacement": p.replacement, "priority": p.priority}
        for p in _PATTERN_REGISTRY
    ]


# ── Built-in Patterns (registered at module load) ──────────────────────────

_BUILTINS: list[tuple[str, str, str, int, re.RegexFlag]] = [
    # --- API keys (match early so they aren't partially caught by email) ---
    (
        "aws_key",
        r"(?<![A-Za-z0-9])(AKIA[0-9A-Z]{16})(?![A-Za-z0-9])",
        "[AWS_KEY_REDACTED]",
        5,
        re.IGNORECASE,
    ),
    (
        "openai_key",
        r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])",
        "[OPENAI_KEY_REDACTED]",
        5,
        re.IGNORECASE,
    ),
    (
        "generic_api_key",
        # Catches patterns like key=ABCDEF1234567890 or token: abcdef...
        r"(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)"
        r"[\s:=]+['\"]?([A-Za-z0-9_\-]{20,})['\"]?",
        "[API_KEY_REDACTED]",
        10,
        re.IGNORECASE,
    ),
    # --- SSN (xxx-xx-xxxx) ---
    (
        "ssn",
        r"\b(\d{3}-\d{2}-\d{4})\b",
        "[SSN_REDACTED]",
        15,
        re.IGNORECASE,
    ),
    # --- Credit card numbers (13-19 digits, optionally separated by - or space) ---
    (
        "credit_card",
        r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,7})\b",
        "[CREDIT_CARD_REDACTED]",
        20,
        re.IGNORECASE,
    ),
    # --- Email addresses ---
    (
        "email",
        r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b",
        "[EMAIL_REDACTED]",
        25,
        re.IGNORECASE,
    ),
    # --- Phone numbers (US and international) ---
    (
        "phone",
        r"(?<![0-9])(\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?![0-9])",
        "[PHONE_REDACTED]",
        30,
        re.IGNORECASE,
    ),
    # --- IPv6 (simplified — 8 hex groups or :: compressed) ---
    (
        "ipv6",
        r"\b((?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4})\b",
        "[IPV6_REDACTED]",
        35,
        re.IGNORECASE,
    ),
    # --- IPv4 ---
    (
        "ipv4",
        r"\b((?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)){3})\b",
        "[IPV4_REDACTED]",
        40,
        re.IGNORECASE,
    ),
]


def _register_builtins() -> None:
    """Populate the registry with the default pattern set."""
    for name, pattern, replacement, priority, flags in _BUILTINS:
        register_pattern(name, pattern, replacement, priority=priority, flags=flags)


_register_builtins()


# ── Public API ──────────────────────────────────────────────────────────────

def redact(text: str, *, enforce: bool = True) -> RedactionResult:
    """Scan *text* for PII and return a ``RedactionResult``.

    Parameters
    ----------
    text:
        Arbitrary input string to scan.
    enforce:
        When ``True`` (default), all detected PII is replaced with
        placeholders.  When ``False``, the scan still runs and
        ``redacted_items`` is populated, but ``clean_text`` contains the
        original text unchanged (useful for dry-run / audit mode).

    Returns
    -------
    RedactionResult
        Includes the sanitised text, a list of every match, count, and
        whether the text was originally clean.
    """
    if not text:
        return RedactionResult(clean_text=text, is_clean=True)

    items: list[RedactedItem] = []
    working = text

    for pii_pattern in _PATTERN_REGISTRY:
        # We search the *current* working text so earlier replacements don't
        # produce false positives for later patterns.
        matches = list(pii_pattern.pattern.finditer(working))
        if not matches:
            continue

        # Process matches in reverse order so replacement indices stay valid.
        for m in reversed(matches):
            matched_text = m.group(0)
            items.append(
                RedactedItem(
                    type=pii_pattern.name,
                    original=matched_text,
                    replacement=pii_pattern.replacement,
                )
            )
            if enforce:
                working = working[: m.start()] + pii_pattern.replacement + working[m.end() :]

    # Items were appended in reverse order per pattern; flip for chronological.
    items.reverse()

    return RedactionResult(
        clean_text=working,
        redacted_items=items,
        redacted_count=len(items),
        is_clean=len(items) == 0,
    )
