# Parishad — Autonomous "Company Brain" & Decision Ledger

> Inspired by **YC 2026 RFS (The Company Brain)** & **Multi-Agent Consensus Architectures**

[![Status](https://img.shields.io/badge/System%20Checks-12%2F12%20PASS-emerald)](#automated-system-verification)
[![Architecture](https://img.shields.io/badge/Architecture-FastAPI%20%2B%20Council%20of%20Agents%20%2B%20Hybrid%20RAG-indigo)](#architecture)
[![Privacy](https://img.shields.io/badge/Privacy-Human--in--the--Loop%20Gate-amber)](#human-in-the-loop-gate)

---

## 💡 The Problem

- **Small Teams (Student Teams, Hackathons & Fast Agencies):** A 5-person engineering team forgets why an architecture or technical limit was chosen two weeks ago because the rationale was lost in Slack chats, PR comments, and half-written documents.
- **Enterprises:** Massive productivity loss as critical domain rules live in senior staff's heads or buried tickets. Official documentation falls out of sync with code changes, making technical onboarding slow, risky, and expensive.

---

## 🚀 How Parishad Works

```
 Incoming Updates (PRs, Slack, Jira, Drive)
                  │
                  ▼
   1. Privacy & Injection Guard
      (Redacts PII, blocks prompt injections & SQLi)
                  │
                  ▼
   2. Signal-vs-Noise & Universal Fact Extractor
      (Filters chatter; extracts numbers, units, budgets, tolerances)
                  │
                  ▼
   3. Hybrid RAG Search Engine
      (BM25 Lexical + TF-IDF Overlap + 768-dim Vector Cosine Similarity)
                  │
                  ▼
   4. "Council of Agents" Multi-Agent Consensus
      ┌────────────────────────────────────────────────────────┐
      │ • Stage 1: First Opinion Agent (Conflict & magnitude)  │
      │ • Stage 2: Cross-Review Agent (Cascading impacts)      │
      │ • Stage 3: Synthesis Agent (Executive summary & draft) │
      └────────────────────────────────────────────────────────┘
                  │
          ┌───────┴───────┐
          │               │
      [Compliant]    [Conflict]
          │               │
     Auto-Passed          ▼
                  "Drafts Only" UI
                  (Human-in-the-Loop Gate)
                          │
                   [Human Signs Off]
                          ▼
            • Official Doc Updated (v2.4 → v2.5)
            • Immutable Decision Ledger Entry (SHA-256)
```

---

## 📦 Project Structure

```
d:\parishad
│
├── run.py                          # 1-click server runner (uvicorn on port 8000)
├── docker-compose.yml              # Multi-container orchestration
├── .env.example                    # Environment configuration template
│
├── frontend/
│   └── index.html                  # Full-featured SPA (Tailwind + Editorial UI)
│
└── backend/
    ├── requirements.txt            # Python dependencies (FastAPI, SQLAlchemy, etc.)
    ├── Dockerfile                  # Production container definition
    └── app/
        ├── config.py               # Pydantic BaseSettings (Gemini, Webhooks, DB)
        ├── main.py                 # FastAPI application factory & static mount
        │
        ├── database/
        │   ├── models.py           # SQLAlchemy async ORM models
        │   ├── connection.py       # aiosqlite engine & initial data seeder
        │   └── vector_store.py     # In-memory vector store with SQLite persistence
        │
        ├── privacy/
        │   ├── pii_redactor.py     # Regex & heuristics redacting emails, keys, SSNs, IPs
        │   └── injection_guard.py  # 15+ attack pattern detector & threat scorer
        │
        ├── extraction/
        │   ├── fact_extractor.py   # Universal numeric + unit extractor (V, Mbps, $, days, etc.)
        │   ├── signal_filter.py    # LLM & keyword signal classifier (decision vs noise)
        │   └── entity_resolver.py  # Normalizes PRs (#123), tickets (PROJ-1), docs (DOC-*)
        │
        ├── rag/
        │   ├── embeddings.py       # Gemini text-embedding-004 + 768-dim offline fallback
        │   └── hybrid_search.py    # BM25 + TF-IDF + Vector ranking with domain boosting
        │
        ├── agents/
        │   ├── opinion_agent.py    # Stage 1: First Opinion conflict & drift evaluator
        │   ├── review_agent.py     # Stage 2: Cross-Review cascading impact auditor
        │   ├── synthesis_agent.py  # Stage 3: Consensus synthesizer & doc drafter
        │   └── council.py          # Multi-agent consensus pipeline orchestrator
        │
        ├── ingestion/
        │   ├── base_connector.py   # Connector ABC with content-hash deduplication
        │   ├── github_connector.py # PR merges, pushes, HMAC-SHA256 signature verifier
        │   ├── slack_connector.py  # Slack Events API & url_verification challenge
        │   ├── jira_connector.py   # Jira issue transitions + Linear webhook support
        │   └── gdrive_connector.py # Google Drive push notifications & doc sync
        │
        └── api/
            ├── routes_check.py     # View 1: Autonomous check pipeline endpoint
            ├── routes_approvals.py # View 2: Human-in-the-Loop review & atomic commit
            ├── routes_brain.py     # View 3: Grounded Q&A assistant & query audit
            ├── routes_docs.py      # View 3: Official specifications CRUD
            ├── routes_ledger.py    # View 3: Tamper-evident ledger & JSON export
            ├── routes_system.py    # View 4: 12 automated E2E tests & SQL explorer
            └── routes_ingestion.py # REST webhook endpoints for external tools
```

---

## ⚡ Quick Start

### 1. Install Dependencies
```powershell
pip install -r d:\parishad\backend\requirements.txt
```

### 2. Start Parishad
```powershell
python d:\parishad\run.py
```

Open your browser to:
- **Web UI:** [http://localhost:8000](http://localhost:8000)
- **Interactive OpenAPI Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

## 🛡️ The 4 Functional Pillars

### 1. Check an Update (`/api/v1/check`)
Paste any incoming pull request, meeting note, or Slack thread. The system runs:
- **Privacy & Injection Guard:** Strips emails, AWS/OpenAI keys, and blocks adversarial prompt overrides.
- **Universal Fact Extractor:** Extracts physical, temporal, and financial parameters (`54V → 60V`, `30 days → 14 days`, `$5000 → $8500`).
- **Hybrid RAG Search:** Matches against official company documents and specifications.
- **Council of Agents:** Runs a 3-stage consensus pipeline to evaluate drift severity and draft an updated specification.

### 2. Human-in-the-Loop Gate (`/api/v1/approvals`)
- **Strict "Drafts Only" Principle:** AI never silently overwrites company specifications.
- **Side-by-Side Diffing:** Compares certified text vs proposed changes with editable wordings.
- **Atomic Commits:** Human approval increments document version, updates active rules, and records an immutable SHA-256 entry.

### 3. Company Brain & Decision Ledger (`/api/v1/brain`, `/api/v1/ledger`)
- **Source-Grounded Assistant:** Answers questions strictly based on indexed specifications.
- **Immutable Ledger:** Every approved or declined decision is cryptographically hashed with SHA-256 for audits.
- **Instant JSON Export:** One-click download of the complete compliance and decision audit bundle.

### 4. System Tests & SQL Console (`/api/v1/system`)
- **12 Automated E2E Tests:** Runs live automated tests verifying every subsystem.
- **SQL Explorer:** Direct read-only SQL queries against `official_docs`, `pending_approvals`, and `decision_ledger`.

---

## 🧪 Automated System Verification (12/12 PASS)

| Test ID | Subsystem Verified | Validation Output | Status |
|---|---|---|---|
| **TEST-01** | Prompt Injection & Hack Blocker | `safe=False, threat_score=0.95` | **✓ PASS** |
| **TEST-02** | Automatic Email & Secret Key Hiding | `redacted=2 items` | **✓ PASS** |
| **TEST-03** | Hardware Voltage Extractor (V) | `proposed=60.0V, change=True` | **✓ PASS** |
| **TEST-04** | Business Policy Extractor (Days) | `proposed=14.0 days` | **✓ PASS** |
| **TEST-05** | Financial Budget Extractor ($ USD) | `proposed=$8500.0` | **✓ PASS** |
| **TEST-06** | Casual Team Chatter Filter | `has_signal=False` | **✓ PASS** |
| **TEST-07** | Semantic Hybrid Document Matcher | `matched=DOC-BMS-402 (score=0.96)` | **✓ PASS** |
| **TEST-08** | Rule Conflict & Drift Detector | `conflict=True (11.1% delta)` | **✓ PASS** |
| **TEST-09** | Compliant Document Verification | `conflict=False, status=ALIGNED` | **✓ PASS** |
| **TEST-10** | Human-in-the-Loop Approval Staging | `drafts_ready=2` | **✓ PASS** |
| **TEST-11** | Decision History SHA-256 Audit Trail | `verified_rows=3, sha256 valid` | **✓ PASS** |
| **TEST-12** | Council of Agents 3-Stage Consensus | `stages=3/3, verdict=CONFLICT_DETECTED` | **✓ PASS** |
