# Parishad — Local Setup & Deployment Guide

This guide provides instructions for setting up, configuring, running, testing, and fine-tuning Parishad (Parishad) locally and in cloud environments.

---

## 1. Prerequisites

- **Operating System:** Windows 10/11, macOS (12+), or Linux (Ubuntu 22.04+ LTS recommended).
- **Python:** Python 3.11 or 3.12 (tested and verified on Python 3.12.10).
- **Package Manager:** `pip` (bundled with Python) and virtual environment support (`venv`).
- **Hardware Requirements:**
  - **Core Application & Deterministic Rule Engine:** 2 CPU cores, 4 GB RAM, 500 MB disk space.
  - **Review Specialist Local Inference (Optional, 4B Qwen3.5):** 16 GB system RAM or NVIDIA GPU with ≥ 8 GB VRAM (CUDA 12+).
  - **Review Specialist Fine-Tuning:** Google Colab T4 / A100 / L4 GPU or local 16GB+ VRAM GPU.

---

## 2. Installation & Environment Configuration

### Step 1: Clone the Repository
```bash
git clone https://github.com/GovindTripathi22/Parishad.git
cd Parishad
```

### Step 2: Create and Activate a Virtual Environment
```bash
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Required Dependencies
```bash
pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt
```

*(Optional: For local inference and fine-tuning toolsets)*
```bash
pip install torch transformers peft accelerate bitsandbytes
```

---

## 3. Environment Variables (`.env`)

Create a `.env` file in the project root directory (`d:/parishad/.env` or root):

```dotenv
# ==============================================================================
# DATABASE CONFIGURATION
# ==============================================================================
# The backend automatically resolves CANONICAL_DB_FILE to project root parishad.db
DATABASE_URL=sqlite+aiosqlite:///./parishad.db

# ==============================================================================
# EXTERNAL AI / EMBEDDINGS (ADVISORY ONLY)
# ==============================================================================
# Optional Gemini API key. If unset, system uses deterministic hash embeddings
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001

# ==============================================================================
# PARISHAD REVIEW SPECIALIST (LOCAL LLM)
# ==============================================================================
# Set to 'true' once fine-tuned weights or local adapters are loaded
PARISHAD_LOCAL_SUMMARY_ENABLED=false
PARISHAD_SPECIALIST_MODEL_PATH=models/parishad_review_specialist
PARISHAD_SPECIALIST_DEVICE=auto
PARISHAD_SPECIALIST_TIMEOUT_SECONDS=15.0

# ==============================================================================
# SECURITY & ACCESS CONTROL (RBAC)
# ==============================================================================
SECRET_KEY=parishad-dev-secret-change-in-production-env
ADMIN_API_KEY=parishad-admin-key-2026

# ==============================================================================
# INGESTION WEBHOOKS & CONNECTORS (OPTIONAL)
# ==============================================================================
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
JIRA_BASE_URL=
JIRA_API_TOKEN=
JIRA_USER_EMAIL=
GDRIVE_CREDENTIALS_JSON=
```

---

## 4. Running the Application

### Option A: Using the Convenience Launcher
```bash
python run.py
```

### Option B: Using Direct Uvicorn Command
```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once started:
- **Web UI & Console:** [http://localhost:8000/](http://localhost:8000/)
- **Interactive OpenAPI Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Detailed Health Diagnostics:** [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

## 5. Running the Automated Test Suite

Parishad enforces strict regression and security verification. All 41 automated tests can be executed via:

```bash
python -m pytest -v
```

### Targeted Test Suites
- **Backend Core & E2E Tests (11 tests):**
  ```bash
  python -m pytest backend/tests/test_backend.py -v
  ```
- **Specialist Summarizer & Fallback Tests (19 tests):**
  ```bash
  python -m pytest backend/tests/test_specialist.py -v
  ```
- **Audit, RBAC, Ledger Verification, & Injection Tests (11 tests):**
  ```bash
  python -m pytest backend/tests/test_audit_and_security.py -v
  ```

---

## 6. Training the Parishad Review Specialist

The repository includes a complete data generation, schema validation, and training workflow for fine-tuning `Qwen/Qwen3.5-4B` using Supervised Fine-Tuning (SFT) and LoRA.

### Step 1: Generate the Training Dataset
```bash
python training/data/generate_dataset.py
```
This generates:
- `training/data/train.jsonl` (840 validated examples)
- `training/data/validation.jsonl` (180 validated examples)
- `training/data/test.jsonl` (180 validated examples)
- `training/data/challenge_cases.jsonl` (105 edge cases)

### Step 2: Validate the Dataset Integrity
```bash
python training/data/validate_dataset.py
```
Enforces strict 10-point programmatic validation:
1. Valid JSON formatting.
2. Mandatory fields (`example_id`, `category`, `facts`, `findings`, `expected_summary`).
3. Finding ID uniqueness and non-empty evidence arrays.
4. Valid severity classification enums (`critical`, `high`, `medium`, `low`, `info`).
5. Absence of fabricated data in synthetic sets.

### Step 3: Run the Training Notebook in Google Colab
1. Open Google Colab and upload `training/parishad_review_training.ipynb`.
2. Connect to a GPU runtime (T4, V100, A100, or L4).
3. Execute the cells sequentially:
   - Cell 1-2: Detects hardware, VRAM, and system RAM.
   - Cell 3-6: Validates JSONL datasets and loads tokenizer.
   - Cell 7-10: Configures 4-bit/8-bit LoRA target modules (`q_proj`, `k_proj`, `v_proj`, `o_proj`).
   - Cell 11-13: Runs SFT with evaluation loss tracking and checkpointing to Google Drive.
   - Cell 14-17: Evaluates schema conformance and finding preservation on held-out test splits.
   - Cell 18: Exports final LoRA adapter artifacts.

### Step 4: Deploy the Trained Adapter Locally
Download the trained adapter folder from Google Drive into the Parishad directory:
```
parishad/
└── models/
    └── parishad_review_specialist/
        ├── adapter_config.json
        ├── adapter_model.safetensors
        ├── special_tokens_map.json
        ├── tokenizer_config.json
        └── tokenizer.json
```
Update `.env`:
```dotenv
PARISHAD_LOCAL_SUMMARY_ENABLED=true
PARISHAD_SPECIALIST_MODEL_PATH=models/parishad_review_specialist
```
Restart the server. The backend will lazily load and cache the model on first inference or startup.
