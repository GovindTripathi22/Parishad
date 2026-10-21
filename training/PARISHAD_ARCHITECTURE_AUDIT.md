# Parishad Architecture Audit

**Repository:** `https://github.com/GovindTripathi22/Parishad` (Local root: `D:\cortexos`)  
**Audit Date:** October 2026  
**Auditor:** Parishad Review Specialist Engineering Team  

---

## Executive Summary

Parishad (internally codebase-named CortexOS) is an autonomous "Company Brain" and human-in-the-loop decision ledger. Its primary purpose is verifying proposed technical and business changes (from Pull Requests, Slack messages, Jira tickets, and Google Docs) against certified company specifications, official rules, system dependencies, and historical decisions.

This document details the architectural audit conducted prior to training and integrating the **Parishad Review Specialist** language model.

---

## 1. How a Proposed Change Enters Parishad

A proposed update enters the system through either:
1. **Interactive Web UI / REST API (`/api/v1/check` or `/api/v1/check/quick`):**
   - Payload: `CheckRequest` containing `message` or `content` (the text of the proposed update), `source` (e.g., `GitHub Pull Request (#418)`, `Slack Team Chat`, `Jira Ticket`), `topic`/`category` (e.g., `BMS-48V`, `COMPANY-OPS`, `AUTO`), and `enforce_privacy` (boolean).
2. **Ingestion Webhooks (`/api/v1/ingestion/...`):**
   - Endpoints for GitHub PR merges/commits (`/github`), Slack Events API (`/slack`), Jira/Linear webhook transitions (`/jira`), and Google Drive notifications (`/gdrive`). Each verifies cryptographic signatures (HMAC-SHA256) and normalizes incoming payloads into standard check requests.

---

## 2. Components Extracting Facts and Retrieving Official Evidence

The ingest and verification pipeline executes through strict sequential stages in `app/api/routes_check.py`:

```
Incoming Proposed Change
          │
          ▼
┌──────────────────────────────────────┐
│ 1. Injection & Security Guard       │ -> app/privacy/injection_guard.py
│    (15+ regex patterns, prompt      │    Rejects jailbreaks, overrides, exfiltration.
│     injection & threat scoring)      │    If threat_score >= 0.5, returns verdict="blocked".
└──────────────────────────────────────┘
          │ (If safe)
          ▼
┌──────────────────────────────────────┐
│ 2. Privacy Redaction Guard           │ -> app/privacy/pii_redactor.py
│    (PII & Secret Sanitizer)          │    Redacts emails, AWS/OpenAI keys, IPv4/v6, SSNs.
└──────────────────────────────────────┘
          │ (Clean text)
          ▼
┌──────────────────────────────────────┐
│ 3. Universal Fact Extractor          │ -> app/extraction/fact_extractor.py
│    (Physical, temporal, financial)   │    Extracts numbers, canonical units (V, ms, Mbps,
│                                      │    USD, days), change patterns ("54V to 60V").
└──────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────┐
│ 4. Entity Resolver                   │ -> app/extraction/entity_resolver.py
│    (Identifiers normalizer)          │    Normalizes #123, PROJ-1, DOC-BMS-402, URLs.
└──────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────┐
│ 5. Hybrid RAG Search Engine          │ -> app/rag/hybrid_search.py
│    (BM25 + TF-IDF + Vector Cosine)   │    Searches active OfficialDoc database entities.
│                                      │    Applies topic and unit domain boosting.
└──────────────────────────────────────┘
```

---

## 3. How Conflicts and Dependency Impacts Are Currently Identified

1. **Conflict & Tolerance Drift (`app/agents/opinion_agent.py`):**
   - Matches the extracted proposed fact's canonical unit (e.g., `v`, `days`, `mbps`) against official `DocRule` entries of the top retrieved document.
   - Calculates mathematical drift:
     $$\Delta\% = \frac{|\text{proposed} - \text{official}|}{\text{official}} \times 100$$
   - Maps drift percentage to discrete severities:
     - $> 50\%$ shift $\rightarrow$ `critical`
     - $> 20\%$ shift $\rightarrow$ `high`
     - $> 5\%$ shift $\rightarrow$ `medium`
     - $\le 5\%$ shift $\rightarrow$ `low`
   - If $\text{official} \neq \text{proposed}$, flags `has_conflict = True`.

2. **Cascading Dependency Impacts (`app/agents/review_agent.py`):**
   - Scans all active company specifications for linked physical or operational concerns:
     - *Voltage changes* $\rightarrow$ checks thermal specs and MOSFET breakdown thresholds (`DOC-BMS-402`).
     - *Bus speed changes* $\rightarrow$ checks twisted-pair EMC shielding and CAN-FD bandwidth standards (`DOC-RAD-118`).
     - *Refund policy changes* $\rightarrow$ checks financial reserve allocations and SLA impacts (`DOC-POLICY-101`).
   - Cross-references the `decision_ledger` table for historical precedents on similar parameters.

3. **Consensus Synthesis (`app/agents/synthesis_agent.py`):**
   - Produces final consensus verdict (`CONFLICT_DETECTED` vs `ALIGNED`).
   - Formulates draft replacement text for the official specification.
   - Assembles an actionable verification checklist for human reviewers.

---

## 4. Boundary: Deterministic Python Logic vs. Generative AI

| Pipeline Stage | Implementation | Behavior |
|---|---|---|
| **Prompt Injection Protection** | Deterministic Python | Regex pattern scanning, heuristic scoring (`threat_score`). |
| **PII & Credential Redaction** | Deterministic Python | Regex replacement (`[EMAIL_REDACTED]`, `[API_KEY_REDACTED]`). |
| **Fact & Unit Extraction** | Deterministic Python | Regex unit normalization, transition detection (`X -> Y`). |
| **Hybrid Document Retrieval** | Deterministic Python | BM25 lexical + TF-IDF token overlap + 768-dim cosine similarity. |
| **Conflict & Delta Calculation**| Deterministic Python | Float arithmetic, percentage delta, threshold severity assignment. |
| **Cascading Dependency Links** | Deterministic Python | Rule/keyword cross-reference across document contents and ledger. |
| **First Opinion Rationale** | **Optional Generative AI** | Calls Gemini 2.5 Flash if `GEMINI_API_KEY` exists; falls back to deterministic formatted string. |
| **Synthesis Executive Summary** | **Optional Generative AI** | Calls Gemini 2.5 Flash if `GEMINI_API_KEY` exists; falls back to deterministic template string. |
| **Document Revision Drafting** | Deterministic Python | Regex substitution of old numeric value with proposed value. |
| **Pending Approval Staging** | Deterministic Python | SQLite insert transaction with `status="PENDING"`. |
| **Ledger Inscription** | Deterministic Python | SHA-256 cryptographic hash computation and commit. |

---

## 5. Information Produced by Existing Agents

1. **`OpinionResult` (Stage 1 First Opinion):**
   - `has_conflict` (bool): True if numerical or policy constraint violated.
   - `severity` (str): `low`, `medium`, `high`, `critical`.
   - `parameter_label` (str): Parameter under review (e.g., `Max Voltage Limit`).
   - `official_value` (str): Certified baseline value (e.g., `54.0 V`).
   - `proposed_value` (str): Incoming proposed value (e.g., `60.0 V`).
   - `delta_pct` (float): Percentage difference.
   - `reasoning` (str): Technical explanation.
   - `confidence` (float): Confidence score (0.0 - 1.0).

2. **`ReviewResult` (Stage 2 Cross-Review):**
   - `opinion_confirmed` (bool): True if Stage 1 verdict validated.
   - `cascading_impacts` (list[str]): Downstream systems or specs affected.
   - `precedent_decisions` (list[str]): Historical ledger rulings on related parameters.
   - `risk_assessment` (str): Evaluated system risk (`low`, `medium`, `high`).
   - `additional_docs_affected` (list[str]): Document IDs referenced.
   - `review_notes` (str): Analytical commentary.
   - `confidence` (float).

3. **`SynthesisResult` (Stage 3 Synthesis):**
   - `final_verdict` (str): `CONFLICT_DETECTED` or `ALIGNED`.
   - `confidence` (float).
   - `human_summary` (str): Executive summary of the change.
   - `proposed_doc_update` (str): Full text of document with proposed replacement.
   - `risk_level` (str): Overall risk.
   - `recommended_action` (str): `STAGE_APPROVAL` or `AUTO_PASS`.
   - `review_checklist` (list[str]): Action items for reviewer sign-off.

---

## 6. How Reviews and Approvals Are Stored

- **Staging Table (`pending_approvals`):**
  When a conflict is detected in `/api/v1/check`, a new row is staged:
  - `draft_id`: Unique identifier (e.g., `REV-103`).
  - `source`, `topic`, `target_doc_id`: Metadata of incoming request.
  - `parameter_label`, `unit`, `old_value`, `new_value`, `numeric_new`: Quantitative difference.
  - `reason`: Executive summary explaining why approval is required.
  - `old_text` vs `proposed_text`: Side-by-side diff text.
  - `status`: Strictly initialized to `"PENDING"`.
  - `council_opinion`: Serialized JSON of Stage 1 & Stage 2 outputs.
  - `council_confidence`: Aggregated confidence metric.

- **Human-in-the-Loop Sign-off (`/api/v1/approvals/{draft_id}/approve`):**
  - Requires human authentication and optional modified wording.
  - In a single atomic database transaction:
    1. `PendingApproval.status` is set to `"APPROVED"` with timestamp and reviewer note.
    2. `OfficialDoc.content` is updated to approved text.
    3. `OfficialDoc.version` is incremented (e.g., `v2.4` $\rightarrow$ `v2.5`).
    4. Corresponding `DocRule.value` is updated to new numeric value.
    5. A new `DecisionLedgerEntry` row is inscribed with an immutable SHA-256 hash chaining doc and values.

---

## 7. Current Assessment: Working, Simulated, Incomplete, Needs Improvement

### What is Working Well:
- **Rock-solid determinism:** Fact extraction, regex unit conversions, PII redaction, and prompt injection guards are fully functional and covered by tests.
- **Human-in-the-loop integrity:** The system strictly enforces the "Drafts Only" principle; neither Gemini nor background jobs can modify certified documents without human sign-off.
- **Fast, reliable SQLite architecture:** Async SQLAlchemy handles concurrent sessions cleanly with automatic table migration and startup seeding.

### What is Simulated or Heuristic:
- **Gemini Summaries are unstructured free-text:** Gemini is only invoked for 2-sentence prose summaries. It does not return structured findings, evidence traceability, or categorized improvements.
- **Single-finding assumption:** The current `routes_check.py` staging logic assumes one primary parameter conflict per request. Complex multi-finding reviews (e.g., voltage conflict + thermal budget breach + unit mismatch in the same PR) collapse into a single draft.
- **Cross-document dependency rules are hardcoded heuristics:** Keyword checks like `"voltage"` triggering `"thermal"` are hardcoded in `review_agent.py`.

### What Needs Improvement:
- **Lack of structured review contract:** Upstream agents produce free-form strings rather than a typed, schema-compliant JSON report that preserves every finding, cites exact evidence, separates facts from inferences, and lists actionable improvements.
- **External API dependency:** When running without internet or without a Gemini API key, summary quality degrades to static string concatenation.

---

## 8. Integration Architecture for Parishad Review Specialist

The new fine-tuned model (**Parishad Review Specialist**) must be integrated cleanly as a **Review Summary Provider** without disrupting existing fact extraction, RAG retrieval, rule checks, or human approval workflows.

### Insertion Point & Workflow:

```
[Incoming Request]
       │
       ▼
[Privacy Guard & Fact Extraction]
       │
       ▼
[Hybrid RAG & Council of Agents]
  • Extracted Facts & Units
  • Matched Official Docs & Rules
  • Opinion Drift & Severity
  • Cascading Impacts & Ledger Precedents
       │
       ▼
┌────────────────────────────────────────────────────────┐
│ Parishad Review Specialist Provider                    │
│ (LocalReviewSummaryProvider / ReviewSummaryService)    │
│                                                        │
│ 1. Assemble structured Review Input Payload            │
│ 2. If PARISHAD_LOCAL_SUMMARY_ENABLED=true:            │
│      Invoke fine-tuned Qwen model (Transformers/LoRA)  │
│      Parse output JSON into ParishadReviewReport       │
│ 3. Strict Backend Completeness & Contract Validation:  │
│      - Verifies 100% Finding IDs preserved             │
│      - Verifies severity consistency and evidence      │
│ 4. Fallback Path:                                      │
│      If model disabled, times out, or drops findings,  │
│      generate Deterministic Fallback Report            │
└────────────────────────────────────────────────────────┘
       │
       ▼
[Enriched CheckResult + PendingApproval Staging]
       │
       ▼
[Existing Frontend UI / REST API Response]
```

### Key Guarantees:
1. **Configurable Opt-In:** Governed by `PARISHAD_LOCAL_SUMMARY_ENABLED` (defaults to `False`).
2. **Zero Finding Loss:** The backend validator independently audits finding completeness. If any finding ID is dropped, the deterministic fallback immediately synthesizes a complete report.
3. **Approval Safety Preserved:** The Specialist only generates recommendations and summaries; all specification changes still require human approval via `/api/v1/approvals`.
