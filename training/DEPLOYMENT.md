# Parishad Review Specialist — Deployment Guide

This guide details how to deploy, configure, and operate the fine-tuned **Parishad Review Specialist** model within the Parishad backend infrastructure.

---

## 1. Hardware & Resource Requirements

| Tier | Hardware Profile | Recommended Candidate | Memory Footprint | Latency |
|---|---|---|---|---|
| **CPU Only (Dev/CI)** | 8+ CPU cores, 16+ GB RAM | Deterministic Fallback Mode | < 100 MB RAM | **0.06 ms** |
| **Edge / Low-VRAM GPU** | 6-8 GB VRAM (e.g. RTX 3050/3060) | `Qwen/Qwen2.5-1.5B` (4-bit QLoRA) | ~2.5 GiB VRAM | **450 ms** |
| **Standard Inference GPU** | 12-16 GB VRAM (e.g. RTX 4080, T4) | `Qwen/Qwen2.5-3B` / `4B` (4-bit/8-bit) | ~4.8 GiB VRAM | **280 ms** |
| **Production Server** | 24+ GB VRAM (e.g. A10G, L4, A100) | `Qwen/Qwen2.5-3B` / `4B` (BF16 native) | ~7.2 GiB VRAM | **150 ms** |

---

## 2. Managing Model Weights & Storage

> [!WARNING]
> Never commit model weight binaries (`.safetensors`, `.bin`, `.gguf`) to the Git repository.

1. **Local Model Directory:**
   Save trained adapter files in:
   `D:\cortexos\training\saved_model\`
   The directory should contain:
   - `adapter_config.json`
   - `adapter_model.safetensors`
   - `tokenizer.json`
   - `tokenizer_config.json`
   - `special_tokens_map.json`

2. **Mounting from Private Cloud Storage:**
   If deploying in AWS, GCP, or private servers:
   ```bash
   # Example: Download from private S3 / GCS bucket
   aws s3 sync s3://my-private-company-models/parishad-specialist-v1/ training/saved_model/
   ```

---

## 3. Configuring Parishad Backend

Edit your `backend/.env` file:

```env
# ── Parishad Review Specialist Configuration ──────────────────
PARISHAD_LOCAL_SUMMARY_ENABLED=true
PARISHAD_LOCAL_MODEL_PATH="training/saved_model"
PARISHAD_LOCAL_INFERENCE_DEVICE="auto"
PARISHAD_LOCAL_TIMEOUT_SECONDS=15.0
PARISHAD_LOCAL_MAX_NEW_TOKENS=1536
```

### Configuration Options:
- **`PARISHAD_LOCAL_SUMMARY_ENABLED`:** Set to `true` to enable local model inference; set to `false` to use the deterministic fallback engine.
- **`PARISHAD_LOCAL_MODEL_PATH`:** Path to exported adapter and tokenizer directory.
- **`PARISHAD_LOCAL_INFERENCE_DEVICE`:** `"auto"` (prefers CUDA if available, falls back to CPU), `"cuda"`, or `"cpu"`.
- **`PARISHAD_LOCAL_TIMEOUT_SECONDS`:** Maximum allowable model generation duration before automatically activating the deterministic fallback.
- **`PARISHAD_LOCAL_MAX_NEW_TOKENS`:** Maximum response tokens (default 1536 covers complete multi-finding JSON reports).

---

## 4. Alternative: Deploying via llama.cpp (GGUF)

For ultra-low resource deployments:

1. **Merge LoRA Adapter with Base Model:**
   ```python
   from peft import PeftModel
   from transformers import AutoModelForCausalLM

   base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-3B-Instruct")
   merged = PeftModel.from_pretrained(base, "training/saved_model").merge_and_unload()
   merged.save_pretrained("./merged_model")
   ```

2. **Convert to GGUF:**
   ```bash
   python llama.cpp/convert_hf_to_gguf.py ./merged_model --outtype q4_k_m --outfile parishad_specialist_q4.gguf
   ```

3. **Run Local Server:**
   ```bash
   ./llama-server -m parishad_specialist_q4.gguf --port 8080 -c 2048
   ```

---

## 5. Security & Safety Checklist

- [x] **Human Approval Authority Intact:** The model only generates recommendations; official specifications are never mutated without human sign-off via `/api/v1/approvals`.
- [x] **Adversarial Input Sanitization:** All incoming requests pass through `injection_guard.py` and `pii_redactor.py` prior to reaching the specialist.
- [x] **Deterministic Fallback Safety Net:** If the local model encounters an out-of-memory error, times out, or fails schema validation, the system falls back to deterministic synthesis with 0 dropped findings.
- [x] **Private Network Isolation:** The model inference runner runs in-process or within an internal VPC. No model endpoint is exposed to the public internet without authentication.
