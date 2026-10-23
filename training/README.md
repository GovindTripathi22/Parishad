# Parishad Review Specialist — Complete Training & Integration Guide

Welcome to the **Parishad Review Specialist** engineering directory.

The Parishad Review Specialist is a domain-adapted language model trained specifically to understand the architecture, terminology, and review workflow of Parishad (Parishad). It takes unstructured proposed updates (from Pull Requests, Slack, Jira, or Google Docs) along with retrieved company specifications and pipeline findings, and generates complete, evidence-grounded, schema-compliant review reports in strict JSON.

---

## 📁 Directory Structure

```
training/
├── README.md                          # This comprehensive guide
├── PARISHAD_ARCHITECTURE_AUDIT.md     # Phase 1 deep architectural audit
├── MODEL_CARD.md                      # Hugging Face-style model documentation
├── DEPLOYMENT.md                      # Local and production deployment guide
├── evaluation_report.md               # Empirical evaluation and benchmark metrics
├── evaluation_summary.json            # Machine-readable benchmark results
├── parishad_review_training.ipynb     # 19-step Google Colab fine-tuning notebook
├── evaluate_model.py                  # CLI evaluation and testing script
├── build_notebook.py                  # Notebook generator
│
└── data/
    ├── parishad_glossary.json         # Phase 2 domain glossary & verified terms
    ├── generate_dataset.py            # Reproducible data-generation pipeline
    ├── validate_dataset.py            # Programmatic dataset validation suite
    ├── DATASET_REPORT.md              # Dataset statistics, coverage, and manual audit
    ├── train.jsonl                    # 800 training records
    ├── validation.jsonl               # 100 validation records
    ├── test.jsonl                     # 100 held-out test records
    └── challenge_cases.jsonl          # 120 adversarial & stress test cases
```

---

## 🎯 What the Specialist Does

Unlike generic code-review LLMs, the Parishad Review Specialist is purpose-built for specification compliance and decision traceability:
1. **100% Finding Preservation:** Every finding ID identified by Parishad (`FIND-01`, `FIND-02`, etc.) is guaranteed to appear in the structured report without omissions or silent merges.
2. **Evidence Traceability:** Cites certified specifications (`DOC-BMS-402`, `DOC-RAD-118`, `DOC-POLICY-101`, etc.) and historical rulings (`DEC-2026-01`, `DEC-2026-02`).
3. **Fact vs. Inference Disambiguation:** Explicitly flags whether a finding is a verified numerical conflict (`is_inference: false`) or an unproven hypothesis (`is_inference: true`).
4. **Actionable Categorized Improvements:** Separates mandatory fixes (`required_fix`) from non-blocking suggestions (`optional_improvement`) and missing verification items (`verification_needed`).
5. **Human Gate Respect:** Does not execute automatic document mutations; review reports serve as drafts for the human approval workflow (`/api/v1/approvals`).

---

## 🚀 Quick Start: Training in Google Colab

1. **Upload Notebook:** Open [Google Colab](https://colab.research.google.com) and upload `training/parishad_review_training.ipynb`.
2. **Upload Dataset:** Upload the contents of `training/data/` (`train.jsonl`, `validation.jsonl`, `test.jsonl`, `challenge_cases.jsonl`).
3. **Select Runtime:** In Colab, select **Runtime > Change runtime type** and choose **T4 GPU** (or A100/L4 if available).
4. **Run All Cells:** The notebook will:
   - Inspect GPU VRAM and system RAM.
   - Automatically configure quantization (4-bit QLoRA on 16GB GPUs, or 16-bit LoRA on 24GB+ GPUs).
   - Run a 2-step smoke test before full training.
   - Fine-tune with cosine learning rate schedule for 3 epochs.
   - Save the best adapter to `./checkpoints/best_adapter` (and optionally backup to Google Drive).
   - Benchmark the fine-tuned adapter against the test set.
   - Export the model to `training/saved_model`.

### Resuming an Interrupted Training Run
If a Colab session disconnects:
- Checkpoints are saved every 25 steps to `./checkpoints/parishad_specialist`.
- In Section 10, set `trainer.train(resume_from_checkpoint=True)`.

---

## 💻 Running Locally and Enabling in Parishad

### 1. Model Storage Location
Keep trained model weights outside Git. Place the exported adapter and tokenizer in:
`D:\parishad\training\saved_model\`

### 2. Enable in `.env`
In `D:\parishad\.env`, add or update:
```env
PARISHAD_LOCAL_SUMMARY_ENABLED=true
PARISHAD_LOCAL_MODEL_PATH="training/saved_model"
PARISHAD_LOCAL_INFERENCE_DEVICE="auto"
PARISHAD_LOCAL_TIMEOUT_SECONDS=15.0
```

### 3. Start Parishad
```powershell
python d:\parishad\run.py
```
When enabled, Parishad's `ReviewSummaryService` will automatically invoke the local fine-tuned model for all check requests (`/api/v1/check`).

### 4. Disabling and Fallback
To disable the local model and return to the lightweight deterministic summary engine:
```env
PARISHAD_LOCAL_SUMMARY_ENABLED=false
```
Even if `PARISHAD_LOCAL_SUMMARY_ENABLED=true`, if the model fails to load, times out, or omits findings, Parishad's **Deterministic Fallback Formatter** activates automatically, ensuring zero service disruption.

---

## 🧪 Automated Testing & Verification

Run the entire test suite (including backend E2E tests, specialist unit tests, and regression checks):
```powershell
pytest backend/tests/test_backend.py backend/tests/test_specialist.py
```

Run the standalone CLI benchmark on held-out and challenge cases:
```powershell
python training/evaluate_model.py
```
