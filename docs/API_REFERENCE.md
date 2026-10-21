# Parishad / CortexOS — API Reference Manual

Complete specification for all REST API endpoints provided by the Parishad backend.

---

## 1. System Health & Diagnostics

### `GET /api/health`
Performs live diagnostic checks across all core and optional subsystems.

- **Authentication:** None
- **Response:** `200 OK`
```json
{
  "status": "healthy",
  "backend_readiness": "ready",
  "database_readiness": "connected",
  "frontend_availability": "available",
  "specialist_model": {
    "enabled": false,
    "status": "disabled",
    "device": "auto",
    "model_path": "models/parishad_review_specialist"
  },
  "gemini_api": {
    "configured": false,
    "model": "gemini-2.5-flash",
    "embedding_model": "gemini-embedding-001"
  },
  "timestamp": "2026-10-22T00:55:00.000000Z"
}
```

---

## 2. Check Pipeline (Conflict & Drift Detection)

### `POST /api/v1/check`
Executes the full 4-stage pipeline:
1. `InjectionGuard` & `PIIRedactor`
2. `FactExtractor` (span-tracked parameter parsing)
3. `HybridSearch` (BM25 + TF-IDF + Gemini/Hash embeddings)
4. Parameter-Aware Rule Matcher & Advisory 3-Stage Council
5. `ParishadReviewSpecialist` Structured Summary Generator
6. Automatic `PendingApproval` staging if drift is detected

- **Headers:** `Content-Type: application/json`
- **Request Body:**
```json
{
  "source": "GitHub PR #402",
  "topic": "BMS-48V",
  "message": "Updating target bus voltage from 48V to 54V for improved high-load efficiency.",
  "enforce_privacy": true
}
```
- **Response:** `200 OK`
```json
{
  "verdict": "CONFLICT_DETECTED",
  "parameter_label": "Operating Voltage",
  "official_value": "48.0V",
  "proposed_value": "54.0V",
  "delta_pct": 12.5,
  "severity": "high",
  "matched_doc": {
    "doc_id": "DOC-BMS-402",
    "title": "Battery Management System Specification",
    "version": 1
  },
  "pipeline_steps": [
    {"name": "Privacy Guard", "status": "passed"},
    {"name": "Key Facts", "status": "passed"},
    {"name": "Rule Match", "status": "conflict"},
    {"name": "Council Consensus", "status": "completed"}
  ],
  "draft_id": "DRAFT-BMS-402-001",
  "specialist_report": {
    "schema_version": "1.0",
    "review_title": "Review: Proposed change to Operating Voltage",
    "overall_status": "blocking",
    "executive_summary": "Policy conflict detected in Operating Voltage: official specification is 48.0V, but proposed value is 54.0V (12.5% delta).",
    "findings": [
      {
        "finding_id": "FINDING-001",
        "category": "policy_conflict",
        "severity": "high",
        "title": "Operating Voltage exceeds official specification",
        "description": "Proposed value 54.0V exceeds official specification 48.0V.",
        "evidence": [
          {
            "source": "DOC-BMS-402",
            "quote": "Target Operating Voltage is 48.0V",
            "location": "Rule: Operating Voltage"
          }
        ],
        "impact": "12.5% change from official baseline 48.0V",
        "recommended_fix": "Align proposed value with official specification (48.0V) or request policy revision.",
        "confidence": "high",
        "is_inference": false,
        "needs_human_verification": true
      }
    ],
    "completeness": {
      "input_finding_count": 1,
      "output_finding_count": 1,
      "missing_finding_ids": [],
      "duplicate_finding_ids": [],
      "all_input_findings_preserved": true
    }
  }
}
```

### `POST /api/v1/check/quick`
Lightweight check bypassing the council stage for low-latency pre-flight checks.
- **Request Body:** Same as `/api/v1/check`
- **Response:** Summary object with `verdict`, `has_conflict`, `facts`, and `top_doc`.

---

## 3. Human Approval Workflow

### `GET /api/v1/approvals`
Lists pending change requests awaiting human review.
- **Query Parameters:** `status` (optional: `PENDING`, `APPROVED`, `DECLINED`)
- **Response:** `200 OK`
```json
[
  {
    "draft_id": "DRAFT-BMS-402-001",
    "doc_id": "DOC-BMS-402",
    "target_doc_version": 1,
    "parameter_label": "Operating Voltage",
    "official_value": "48.0V",
    "proposed_value": "54.0V",
    "delta_pct": 12.5,
    "severity": "high",
    "status": "PENDING",
    "created_at": "2026-10-22T00:50:00Z"
  }
]
```

### `POST /api/v1/approvals/{draft_id}/approve`
Approves a pending update, updates the official document, mutates the active rule, increments the document version, and appends a 64-char SHA-256 chained entry to the Decision Ledger.

- **Headers:** `X-User-Role: REVIEWER` or `X-User-Role: ADMIN`
- **Request Body:**
```json
{
  "note": "Approved after thermal dissipation testing confirmed safety margins.",
  "edited_text": "Target Operating Voltage is 54.0V under revised standard."
}
```
- **Response:** `200 OK`
```json
{
  "status": "APPROVED",
  "draft_id": "DRAFT-BMS-402-001",
  "doc_id": "DOC-BMS-402",
  "new_doc_version": 2,
  "ledger_entry_id": "DEC-2026-004",
  "entry_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000"
}
```
- **Error Responses:**
  - `403 Forbidden`: User does not hold `REVIEWER` or `ADMIN` role.
  - `404 Not Found`: Draft ID does not exist.
  - `409 Conflict`: Target document was modified concurrently (`target_doc_version` mismatch).

### `POST /api/v1/approvals/{draft_id}/decline`
Declines a pending update and records the rejection in the Decision Ledger.
- **Headers:** `X-User-Role: REVIEWER` or `X-User-Role: ADMIN`
- **Request Body:**
```json
{
  "note": "Declined: Exceeds safe operating limit of current capacitor bank."
}
```
- **Response:** `200 OK` (`status: "DECLINED"`)

---

## 4. Decision Ledger & Cryptographic Verification

### `GET /api/v1/ledger`
Returns the sequential audit ledger entries, ordered newest to oldest.
- **Query Parameters:** `limit` (int, default 50)
- **Response:** `200 OK` (list of `DecisionLedgerEntry` objects including `entry_hash`, `prev_hash`, `doc_version`).

### `GET /api/v1/ledger/verify`
Performs an on-demand cryptographic audit of the entire SHA-256 chain from the genesis block to the head entry.
- **Authentication:** None
- **Response:** `200 OK`
```json
{
  "status": "VALID",
  "chain_length": 4,
  "genesis_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "head_hash": "a1b2c3d4e5f6...64chars",
  "verified_at": "2026-10-22T00:55:00Z"
}
```
*If tampering occurred:*
```json
{
  "status": "INVALID",
  "error": "Hash mismatch at block index 2 (DEC-2026-002)",
  "expected_hash": "...",
  "stored_hash": "..."
}
```

### `GET /api/v1/ledger/stats`
Summary analytics: total decisions, approved count, declined count, topic distribution.

### `GET /api/v1/ledger/export`
Exports the entire governance bundle (documents, rules, decision history) as an immutable JSON audit artifact.

---

## 5. Company Brain & Multimodal OCR

### `POST /api/v1/brain/ask`
Hybrid semantic question answering grounded in official documents.
- **Request Body:**
```json
{
  "question": "What is the maximum operating voltage for the BMS?"
}
```
- **Response:** `200 OK`
```json
{
  "answer": "According to DOC-BMS-402, the target operating voltage is 48.0V.",
  "supporting_docs": ["DOC-BMS-402"],
  "confidence": 0.95
}
```

### `POST /api/v1/brain/ocr`
Extracts specification rules from an uploaded datasheet image or document.
- **Request:** `multipart/form-data` with `file: <binary>`
- **Response (when unconfigured):** `503 Service Unavailable`
```json
{
  "error": "OCR_UNAVAILABLE",
  "detail": "Multimodal OCR requires a valid GEMINI_API_KEY. Deterministic extraction is used for text inputs."
}
```

---

## 6. Official Documents CRUD

- `GET /api/v1/docs` — List all active official specifications and their extracted parameters.
- `GET /api/v1/docs/{doc_id}` — Get detailed document metadata and rules.
- `POST /api/v1/docs` — Author a new official document. Requires `ADMIN` role. Automatically parses parameters into `doc_rules`.
- `PUT /api/v1/docs/{doc_id}` — Revise document text and increment version. Requires `ADMIN` role.
- `DELETE /api/v1/docs/{doc_id}` — Soft delete document (`is_active = false`). Requires `ADMIN` role.

---

## 7. System Utilities & SQL Explorer

### `POST /api/v1/system/tests`
Executes Parishad's 12 core end-to-end verification tests server-side and returns execution telemetry.

### `POST /api/v1/system/query`
Audits database state via an allowlisted, read-only SQL Explorer.
- **Headers:** `X-User-Role: ADMIN`
- **Request Body:**
```json
{
  "query": "SELECT doc_id, version, title FROM official_docs"
}
```
- **Response:** `200 OK`
```json
{
  "columns": ["doc_id", "version", "title"],
  "rows": [
    ["DOC-BMS-402", 1, "Battery Management System Specification"],
    ["DOC-REFUND-001", 1, "Customer Refund and SLA Policy"],
    ["DOC-RADAR-77", 1, "Automotive 77GHz Radar Processing Spec"]
  ],
  "row_count": 3
}
```
*Note: Any queries referencing non-allowlisted tables, comments, semicolons, or DDL/DML return an HTTP 400 error.*
