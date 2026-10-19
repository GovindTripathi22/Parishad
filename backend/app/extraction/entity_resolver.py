"""
CortexOS — Entity Resolution & Deduplication.

Extracts structured entity mentions from free-form text, normalises them,
deduplicates, and links document references to known Official Docs in the
Company Brain.

Recognised entity types:
  • ``doc_ref``  — Document IDs matching ``DOC-XXX-NNN`` patterns
  • ``pr``       — Pull request numbers (``#123``, ``PR #456``)
  • ``ticket``   — Project ticket keys (``PROJ-123``, ``INFRA-4567``)
  • ``person``   — Capitalised person names (first + last)
  • ``system``   — Known system / service identifiers (heuristic)

Usage::

    from app.extraction.entity_resolver import resolve_entities

    result = resolve_entities(
        "Alice Smith opened PR #42 referencing DOC-SRE-007 and INFRA-123",
        known_doc_ids=["DOC-SRE-007", "DOC-NET-001"],
    )
    print(result.has_doc_reference)  # True
    print(result.linked_doc_ids)     # ["DOC-SRE-007"]
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field


# ── Data Models ─────────────────────────────────────────────────────────────

class Entity(BaseModel):
    """A single entity mention extracted from text."""
    type: str = Field(
        ...,
        description="Entity category: doc_ref | pr | ticket | person | system.",
    )
    value: str = Field(..., description="Entity as it appeared in the source text.")
    normalized: str = Field(
        ..., description="Canonical / normalised form of the entity.",
    )


class EntityResult(BaseModel):
    """Aggregated entity extraction output."""
    entities: list[Entity] = Field(
        default_factory=list,
        description="All unique entities found, in document order.",
    )
    linked_doc_ids: list[str] = Field(
        default_factory=list,
        description="Doc IDs that matched entries in `known_doc_ids`.",
    )
    has_doc_reference: bool = Field(
        False,
        description="True when at least one DOC-XXX-NNN pattern was found.",
    )


# ── Regex Patterns ──────────────────────────────────────────────────────────

# DOC-XXX-NNN  (e.g. DOC-SRE-007, DOC-INFRA-1234)
_DOC_REF_RE = re.compile(
    r"\b(DOC-[A-Z]{2,10}-\d{1,6})\b",
    re.IGNORECASE,
)

# PR references: #123, PR #123, PR-123, pull request #123
_PR_RE = re.compile(
    r"(?:(?:PR|pull\s+request)\s*#?\s*(\d{1,6}))|(?:(?<!\w)#(\d{1,6})(?!\w))",
    re.IGNORECASE,
)

# Ticket keys: PROJ-123, INFRA-4567  (2-10 uppercase letters + dash + digits)
# Exclude DOC- prefix to avoid double-matching document refs.
_TICKET_RE = re.compile(
    r"\b(?!DOC-)([A-Z]{2,10}-\d{1,6})\b",
)

# Person names: two or more capitalised words in sequence.
# Simple heuristic — won't catch every name but avoids false positives on
# isolated capitalised words like "Monday" or "API".
_PERSON_RE = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,3})\b",
)

# Common non-person capitalised sequences to filter out.
_PERSON_STOP_WORDS = frozenset({
    "Standard Operating", "Operating Procedure", "Pull Request",
    "Load Balancer", "Data Center", "Access Control", "Root Cause",
    "Cloud Function", "Service Mesh", "Machine Learning",
    "Deep Learning", "High Availability", "Business Logic",
    "Best Practice", "Best Practices", "Next Steps", "Action Items",
    "Key Takeaways", "Open Source", "End User", "New York",
    "San Francisco", "Los Angeles", "United States",
})

# System / service names: patterns commonly seen in engineering text.
_SYSTEM_KEYWORDS = frozenset({
    "kubernetes", "k8s", "docker", "redis", "postgres", "postgresql",
    "mongodb", "kafka", "rabbitmq", "elasticsearch", "nginx", "envoy",
    "istio", "grafana", "prometheus", "datadog", "jenkins", "circleci",
    "github actions", "terraform", "pulumi", "cloudflare", "aws",
    "gcp", "azure", "bigquery", "spanner", "dynamodb", "s3",
    "lambda", "cloud run", "app engine", "firebase", "supabase",
    "vercel", "netlify", "heroku", "vault", "consul", "nomad",
})

# Also catch PascalCase service names like AuthService, PaymentGateway.
_SYSTEM_PASCAL_RE = re.compile(
    r"\b([A-Z][a-z]+(?:[A-Z][a-z]+){1,4}(?:Service|Server|Gateway|Proxy|Worker|Manager|Controller|Handler|Client|Queue|Cache|Store|Engine|Pipeline|Cluster|Node|Agent|Daemon|API))\b",
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _normalize_doc_id(raw: str) -> str:
    """Uppercase and strip whitespace from a doc reference."""
    return raw.strip().upper()


def _normalize_ticket(raw: str) -> str:
    """Uppercase ticket keys for consistent matching."""
    return raw.strip().upper()


def _normalize_pr(raw: str) -> str:
    """Normalise PR number to ``#NNN`` form."""
    digits = raw.strip().lstrip("#")
    return f"#{digits}"


def _normalize_person(raw: str) -> str:
    """Title-case and strip extra whitespace."""
    return " ".join(raw.split()).title()


def _normalize_system(raw: str) -> str:
    """Lowercase system identifiers for dedup."""
    return raw.strip().lower()


# ── Public API ──────────────────────────────────────────────────────────────

def resolve_entities(
    text: str,
    known_doc_ids: list[str] | None = None,
) -> EntityResult:
    """Extract, normalise, and deduplicate entity mentions from *text*.

    Parameters
    ----------
    text:
        Free-form text to scan.
    known_doc_ids:
        Optional list of Official Doc IDs currently in the Brain.  When
        provided, any extracted ``doc_ref`` entities that match an entry
        in this list will be added to ``linked_doc_ids``.

    Returns
    -------
    EntityResult
        Deduplicated entities plus linked doc information.
    """
    if not text or not text.strip():
        return EntityResult()

    seen_normalized: set[str] = set()
    entities: list[Entity] = []
    doc_refs_found: list[str] = []

    def _add(entity_type: str, value: str, normalized: str) -> None:
        key = f"{entity_type}::{normalized}"
        if key in seen_normalized:
            return
        seen_normalized.add(key)
        entities.append(Entity(type=entity_type, value=value, normalized=normalized))

    # 1. Document references
    for m in _DOC_REF_RE.finditer(text):
        raw = m.group(1)
        norm = _normalize_doc_id(raw)
        _add("doc_ref", raw, norm)
        doc_refs_found.append(norm)

    # 2. PR numbers
    for m in _PR_RE.finditer(text):
        # group(1) for "PR #N" form, group(2) for bare "#N" form
        digits = m.group(1) or m.group(2)
        if digits:
            raw = m.group(0).strip()
            norm = _normalize_pr(digits)
            _add("pr", raw, norm)

    # 3. Ticket keys (exclude doc refs already captured)
    for m in _TICKET_RE.finditer(text):
        raw = m.group(1)
        norm = _normalize_ticket(raw)
        # Skip if it's already captured as a doc_ref.
        if f"doc_ref::{norm}" not in seen_normalized:
            _add("ticket", raw, norm)

    # 4. Person names
    for m in _PERSON_RE.finditer(text):
        raw = m.group(1)
        if raw in _PERSON_STOP_WORDS:
            continue
        norm = _normalize_person(raw)
        _add("person", raw, norm)

    # 5. System names — keyword scan
    lower_text = text.lower()
    for kw in _SYSTEM_KEYWORDS:
        if kw in lower_text:
            # Find the actual cased version in the text for `value`.
            idx = lower_text.find(kw)
            original = text[idx : idx + len(kw)]
            _add("system", original, _normalize_system(kw))

    # 5b. System names — PascalCase service patterns
    for m in _SYSTEM_PASCAL_RE.finditer(text):
        raw = m.group(1)
        _add("system", raw, _normalize_system(raw))

    # ── Link to known docs ──────────────────────────────────────────────
    known_set: set[str] = set()
    if known_doc_ids:
        known_set = {did.strip().upper() for did in known_doc_ids}

    linked = [did for did in doc_refs_found if did in known_set]

    return EntityResult(
        entities=entities,
        linked_doc_ids=sorted(set(linked)),
        has_doc_reference=len(doc_refs_found) > 0,
    )
