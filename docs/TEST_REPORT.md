# Parishad / CortexOS — Automated Test & Verification Report

This report documents the automated test suite, verification methodologies, and test results for Parishad (CortexOS).

---

## 1. Executive Summary

| Metric | Result | Status |
| :--- | :--- | :--- |
| **Total Automated Pytest Tests** | 42 | **100% Passed** |
| **Execution Duration** | 1.25 seconds | Sub-second test execution per module |
| **Failed Tests** | 0 | None |
| **Skipped Tests** | 0 | None |
| **Pre-existing Failures Fixed** | 3 | Dual-DB split, unpinned router fail-fast, missing schema columns |
| **Automated System Checks** | 12 | **100% Passed** (Server-side E2E suite) |

---

## 2. Test Execution Command & Environment

```bash
cd d:\cortexos
python -m pytest -v
```

### Execution Platform:
- **OS:** Windows 11 (win32)
- **Python Version:** 3.12.10
- **Pytest Version:** 9.1.1
- **Asyncio Plugin:** Mode.AUTO

---

## 3. Test Suite Breakdown

### Suite A: Security, Cryptographic Ledger, & Rule Engine (`test_audit_and_security.py`)
*11 Tests — 100% Passed*

| Test Identifier | Category | Assertion & Verification Description |
| :--- | :--- | :--- |
| `test_rbac_reviewer_required_for_approval` | RBAC Security | Verifies that unauthenticated or non-reviewer users receive `HTTP 403 Forbidden` when attempting to approve changes. Confirms only `REVIEWER` / `ADMIN` roles can sign off. |
| `test_sql_explorer_allows_read_only_allowlisted_table` | SQL Explorer | Validates that legitimate `SELECT` statements on allowlisted tables (`official_docs`, `decision_ledger`) succeed and return structured columnar data. |
| `test_sql_explorer_rejects_union_injection` | SQL Injection | Asserts that `UNION SELECT` payloads designed to extract password hashes or metadata are blocked with `HTTP 400 Bad Request`. |
| `test_sql_explorer_rejects_comments` | SQL Injection | Confirms that SQL comment markers (`--`, `/*`, `*/`) used in filter-evasion attacks are rejected. |
| `test_sql_explorer_rejects_multi_statement` | SQL Injection | Validates that stacked queries using semicolons (e.g. `SELECT 1; DROP TABLE official_docs;`) are blocked immediately. |
| `test_sql_explorer_rejects_forbidden_table` | Table Allowlisting | Ensures queries targeting `sqlite_master`, system catalogs, or unauthorized tables return `HTTP 400`. |
| `test_ledger_cryptographic_verification_endpoint` | Cryptographic Audit | Invokes `GET /api/v1/ledger/verify` to crawl the SHA-256 chained blocks from genesis (`0*64`) to head and asserts `status: "VALID"`. |
| `test_parameter_aware_matcher_rejects_cross_domain_fallback` | Rule Safety | Confirms that when an update contains an unmatched unit/parameter (e.g., radar speed `120km/h` evaluated against BMS battery specs), the system correctly marks the parameter as unmatched rather than falling back to an unrelated rule. |
| `test_zero_baseline_percentage_calculation` | Mathematical Accuracy | Verifies the fix for division-by-zero on zero-baseline rules (e.g., target defects = 0), ensuring `is_zero_baseline = True`, `delta_pct = 100%`, and severity = `critical`. |
| `test_spec_sheet_ocr_honesty_when_unconfigured` | Truthfulness & Honesty | Asserts that `/api/v1/brain/ocr` returns an honest `OCR_UNAVAILABLE` error when `GEMINI_API_KEY` is unset, verifying the removal of fabricated hardcoded fallback values. |
| `test_approval_action_creates_chained_sha256_ledger_entry` | Concurrency & Ledger | Verifies that approving a draft increments the document version, generates a valid 64-char SHA-256 hash linked to the previous ledger entry, and prevents concurrency collisions. |

---

### Suite B: Core Backend & API Workflows (`test_backend.py`)
*12 Tests — 100% Passed*

| Test Identifier | Category | Assertion & Verification Description |
| :--- | :--- | :--- |
| `test_root_health` | System Health | Verifies `GET /api/health` returns HTTP 200 with comprehensive subsystem health status. |
| `test_serve_frontend_index` | Frontend Integration | Confirms that the root endpoint (`GET /`) successfully serves `frontend/index.html`. |
| `test_serve_frontend_feature_pages` | Multi-Page Architecture | Asserts that each dedicated feature page (`/check`, `/approvals`, `/brain`, `/ledger`, `/tests`, `/guide`, `/static/parishad.css`, `/static/parishad.js`) returns HTTP 200 with title and assets. |
| `test_get_docs` | Official Documents | Validates retrieval of seeded official documents and their extracted parameter rules. |
| `test_get_approvals` | Human Safety Gate | Verifies retrieval and schema compliance of pending approval drafts. |
| `test_get_ledger` | Audit Ledger | Ensures retrieval of historical decisions with cryptographic hash strings. |
| `test_check_safety_block` | Prompt Injection Guard | Tests submission of adversarial prompt injections; asserts immediate blocking and protection. |
| `test_check_bms_conflict` | Drift Detection Pipeline | Tests the canonical BMS scenario (48V to 54V); asserts conflict verdict, delta percentage, and auto-staged pending approval. |
| `test_system_health` | System Diagnostics | Tests `GET /api/v1/system/health` diagnostic endpoint. |
| `test_system_stats` | Dashboard Telemetry | Asserts correct document count, pending draft count, and ledger entry totals. |
| `test_system_e2e_suite` | Automated E2E Runner | Executes the 12 system verification checks via `POST /api/v1/system/tests`. |
| `test_brain_ask` | Company Brain | Tests semantic inquiry retrieval over indexed engineering documents. |

---

### Suite C: Review Specialist & Structured Reporting (`test_specialist.py`)
*19 Tests — 100% Passed*

| Test Identifier | Category | Assertion & Verification Description |
| :--- | :--- | :--- |
| `test_deterministic_fallback_valid` | Fallback Generation | Verifies that the deterministic review summary generator produces valid schema JSON preserving all input findings. |
| `test_multiple_independent_findings` | Multi-Finding Handling | Tests generation when multiple distinct findings (e.g. voltage drift + thermal limit) exist; ensures no findings are merged or lost. |
| `test_no_known_conflicts_aligned` | Aligned Updates | Confirms compliant changes generate `overall_status: "no_known_conflict"` with zero blocking errors. |
| `test_insufficient_evidence` | Truthful Reporting | Verifies that updates lacking clear specifications produce `overall_status: "insufficient_evidence"` rather than invented facts. |
| `test_completeness_rejects_dropped_findings` | Schema Completeness | Asserts that the backend validator flags any model output that drops a finding ID as incomplete. |
| `test_completeness_rejects_duplicate_findings` | ID Integrity | Asserts that duplicate finding IDs in generated output are detected and rejected. |
| `test_provider_handles_malformed_json` | Resilient Parsing | Simulates truncated/malformed JSON from an LLM; confirms deterministic repair and fallback activation. |
| `test_provider_disabled_or_unavailable` | Graceful Degradation | Verifies system behaves correctly and cleanly falls back to deterministic summary when the local model is disabled. |
| `test_check_endpoint_includes_specialist_report` | API Integration | Ensures `POST /api/v1/check` embeds the structured specialist report alongside pipeline verdicts. |
| `test_direct_specialist_endpoint` | Dedicated Specialist Route | Tests dedicated review specialist generation endpoint directly. |
| `test_prompt_injection_safety_and_no_side_effects` | Security & Read-Only | Asserts that summary generation never writes to the database, alters doc versions, or permits instruction injection. |
| `test_existing_approval_workflow_unchanged` | Workflow Preservation | Confirms adding the specialist leaves the human approval gate as the sole authority for document updates. |
| `test_unknown_categories_and_severity_labels` | Schema Robustness | Tests handling of unusual category tags and fallback severity mappings. |
| `test_model_inference_timeout_and_load_failure` | Reliability & Timeouts | Simulates inference latency exceeding timeout; confirms immediate transition to deterministic fallback without hanging. |
| `test_long_input_and_complex_payload` | Boundary Stress | Validates execution against complex multi-paragraph change requests with 5+ numeric parameters. |
| `test_prompt_injection_inside_supplied_evidence` | Retrieval Safety | Asserts that malicious text embedded in retrieved documents is treated as untrusted data and does not hijack the summary. |
| `test_privacy_redacted_input_flow` | Privacy Integration | Tests summary generation on inputs sanitized by the PII redactor. |
| `test_empty_content_returns_specialist_report` | Edge Cases | Validates handling of whitespace/empty input with graceful validation error reporting. |
| `test_blocked_attack_returns_specialist_report` | Adversarial Safety | Confirms blocked attacks produce a dedicated security finding report. |

---

## 4. 12 Automated System Checks (`/api/v1/system/tests`)

The built-in verification suite evaluates real-time functionality across all 4 system chapters:

1. **`TEST-PII-01`**: PII Redaction Pattern Matching — **PASS**
2. **`TEST-INJ-02`**: Prompt Injection Threat Boundary — **PASS**
3. **`TEST-EXT-03`**: Numeric & Unit Extraction with Contextual Direction — **PASS**
4. **`TEST-RAG-04`**: Hybrid Search Keyword & Vector Weighting — **PASS**
5. **`TEST-ENG-05`**: Parameter-Aware Rule Matching & Delta Calculation — **PASS**
6. **`TEST-ZERO-06`**: Zero-Baseline Mathematical Stability — **PASS**
7. **`TEST-APP-07`**: Optimistic Concurrency & Version Lock in Approvals — **PASS**
8. **`TEST-LED-08`**: 64-character SHA-256 Hash Chain Integrity Verification — **PASS**
9. **`TEST-RBAC-09`**: Server-side Role Authorization Gate — **PASS**
10. **`TEST-SQL-10`**: AST/Allowlist SQL Query Injection Defense — **PASS**
11. **`TEST-SPEC-11`**: Specialist 100% Finding ID Preservation & Completeness — **PASS**
12. **`TEST-OCR-12`**: Truthful Diagnostic Reporting for Unconfigured Multimodal OCR — **PASS**
