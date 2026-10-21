"""
Parishad Review Specialist — Local Model Provider.
Manages lazy-loading, caching, timeout bounds, and inference for the local fine-tuned model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the Parishad Review Specialist, an expert engineering audit model for Parishad.\n"
    "Your responsibility is to review proposed changes against supplied official specifications, "
    "rules, dependencies, and decision history.\n"
    "You MUST generate a structured JSON review report complying strictly with the Parishad Review Schema.\n"
    "CRITICAL NON-NEGOTIABLE RULES:\n"
    "1. Every finding supplied in the input MUST appear in the 'findings' list with its EXACT finding_id preserved.\n"
    "2. Do not omit, merge, or fabricate finding IDs.\n"
    "3. Only cite evidence directly supplied in the official documents or rules. Do not hallucinate quotes or policies.\n"
    "4. Explicitly distinguish confirmed facts from inferences (is_inference=true/false) and flag required human verification.\n"
    "5. Output valid, parseable JSON only, without markdown wrapping or conversational commentary."
)


class ModelUnavailableError(Exception):
    """Raised when the local model cannot be loaded or is not installed."""
    pass


class LocalReviewSummaryProvider:
    """
    Manages local model lifecycle, singleton caching, and generation.
    Supports HuggingFace Transformers with PyTorch / PEFT adapters.
    """

    _instance: Optional[LocalReviewSummaryProvider] = None
    _model: Any = None
    _tokenizer: Any = None
    _is_loaded: bool = False
    _load_error: Optional[str] = None

    def __init__(self) -> None:
        self.settings = get_settings()

    @classmethod
    def get_instance(cls) -> LocalReviewSummaryProvider:
        if cls._instance is None:
            cls._instance = LocalReviewSummaryProvider()
        return cls._instance

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.PARISHAD_LOCAL_SUMMARY_ENABLED)

    def load_model(self) -> bool:
        """
        Loads and caches the model and tokenizer into memory if enabled.
        Thread-safe singleton initialization.
        """
        if self._is_loaded:
            return True
        if not self.is_enabled:
            logger.info("LocalReviewSummaryProvider is disabled (PARISHAD_LOCAL_SUMMARY_ENABLED=False).")
            return False

        model_path = Path(self.settings.PARISHAD_LOCAL_MODEL_PATH)
        if not model_path.is_absolute():
            # Resolve relative to project root
            base_dir = Path(self.settings.BASE_DIR).parent
            model_path = base_dir / model_path

        if not model_path.exists():
            self._load_error = f"Model directory not found at {model_path}"
            logger.warning("LocalReviewSummaryProvider: %s", self._load_error)
            return False

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            device_setting = self.settings.PARISHAD_LOCAL_INFERENCE_DEVICE.lower()
            if device_setting == "cuda" and torch.cuda.is_available():
                device = "cuda"
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            elif device_setting == "auto":
                if torch.cuda.is_available():
                    device = "cuda"
                    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                else:
                    device = "cpu"
                    dtype = torch.float32
            else:
                device = "cpu"
                dtype = torch.float32

            logger.info("Loading Parishad Review Specialist from %s on device=%s (%s)", model_path, device, dtype)

            tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            adapter_config_path = model_path / "adapter_config.json"
            if adapter_config_path.exists():
                from peft import PeftModel
                with open(adapter_config_path, "r", encoding="utf-8") as f:
                    adapter_cfg = json.load(f)
                base_model_name = adapter_cfg.get("base_model_name_or_path", "Qwen/Qwen3.5-4B")
                logger.info("Detected PEFT adapter. Loading base model: %s", base_model_name)
                base_model = AutoModelForCausalLM.from_pretrained(
                    base_model_name,
                    torch_dtype=dtype,
                    device_map=device if device == "cuda" else None,
                    trust_remote_code=True,
                )
                model = PeftModel.from_pretrained(base_model, str(model_path))
            else:
                model = AutoModelForCausalLM.from_pretrained(
                    str(model_path),
                    torch_dtype=dtype,
                    device_map=device if device == "cuda" else None,
                    trust_remote_code=True,
                )

            if device == "cpu":
                model.to(torch.device("cpu"))
            model.eval()

            self._tokenizer = tokenizer
            self._model = model
            self._is_loaded = True
            self._load_error = None
            logger.info("Parishad Review Specialist model loaded successfully.")
            return True

        except Exception as exc:
            self._load_error = str(exc)
            logger.error("Failed to load local model: %s", exc)
            return False

    def build_prompt(
        self,
        change_request: str,
        source: str,
        topic: str,
        official_docs: list[dict[str, Any]],
        pipeline_findings: list[dict[str, Any]],
        ledger_history: list[dict[str, Any]],
        missing_context: list[str],
        security_warnings: list[str],
    ) -> str:
        """Constructs the user turn matching dataset format."""
        doc_sections = []
        for d in official_docs:
            rules_text = ", ".join([f"{r.get('label')} = {r.get('value')} {r.get('unit')}" for r in d.get("rules", [])])
            doc_sections.append(
                f"--- Official Document: {d.get('doc_id')} ({d.get('title')}, {d.get('version')}) ---\n"
                f"Topic: {d.get('topic')}\n"
                f"Content: {d.get('content')}\n"
                f"Certified Rules: {rules_text}"
            )

        findings_sections = []
        for f in pipeline_findings:
            findings_sections.append(
                f"Finding ID: {f.get('finding_id')}\n"
                f"  Category: {f.get('category')}\n"
                f"  Proposed Value: {f.get('proposed_value', 'N/A')}\n"
                f"  Official Value: {f.get('official_value', 'N/A')}\n"
                f"  Delta: {f.get('delta_pct', 0.0)}%\n"
                f"  Target Doc: {f.get('target_doc_id', 'N/A')}\n"
                f"  Initial Rationale: {f.get('initial_rationale', '')}"
            )

        ledger_lines = [
            f"• {e.get('decision_id')} ({e.get('timestamp')}): {e.get('parameter')} changed from {e.get('old_value')} to {e.get('new_value')} [{e.get('status')}]"
            for e in ledger_history
        ]
        ledger_text = "\n".join(ledger_lines) if ledger_lines else "No prior decisions recorded."

        prompt = (
            f"### PROPOSED CHANGE REQUEST\n"
            f"Source: {source}\n"
            f"Topic: {topic}\n"
            f"Content:\n{change_request}\n\n"
            f"### OFFICIAL DOCUMENTS & RULES SUPPLIED\n"
            f"{chr(10).join(doc_sections)}\n\n"
            f"### PARISHAD PIPELINE FINDINGS (MUST PRESERVE ALL FINDING IDs)\n"
            f"{chr(10).join(findings_sections) if findings_sections else 'No pipeline findings detected (Zero conflicts flagged).'}\n\n"
            f"### DECISION LEDGER PRECEDENTS\n"
            f"{ledger_text}\n"
        )
        if missing_context:
            prompt += f"\n### KNOWN PIPELINE GAPS / MISSING CONTEXT\n" + "\n".join([f"- {m}" for m in missing_context])
        if security_warnings:
            prompt += f"\n### SECURITY ALERTS\n" + "\n".join([f"- {s}" for s in security_warnings])

        prompt += "\n\nPlease analyze this change and generate the complete Parishad Review Report in JSON format."
        return prompt

    @staticmethod
    def extract_json(raw_text: str) -> Optional[dict[str, Any]]:
        """Extracts JSON object from model response, handling markdown blocks if present."""
        text = raw_text.strip()
        # Try raw JSON first
        try:
            return json.loads(text)
        except Exception:
            pass

        # Try markdown code block extraction
        match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass

        # Try scanning for outer braces
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass

        return None

    async def generate_raw(self, user_prompt: str) -> Optional[str]:
        """Runs synchronous model generation in thread pool with bounded timeout."""
        if not self._is_loaded:
            loaded = self.load_model()
            if not loaded or not self._is_loaded:
                raise ModelUnavailableError(self._load_error or "Model is not loaded.")

        timeout_sec = float(self.settings.PARISHAD_LOCAL_TIMEOUT_SECONDS)
        max_tokens = int(self.settings.PARISHAD_LOCAL_MAX_NEW_TOKENS)

        def _sync_generate() -> str:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            if hasattr(self._tokenizer, "apply_chat_template"):
                input_text = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                input_text = f"{SYSTEM_PROMPT}\n\nUser: {user_prompt}\n\nAssistant:"

            inputs = self._tokenizer(input_text, return_tensors="pt")
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            import torch
            with torch.no_grad():
                output_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=0.1,
                    top_p=0.9,
                    do_sample=False,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            # Slice off input tokens
            input_len = inputs["input_ids"].shape[1]
            gen_ids = output_ids[0][input_len:]
            return self._tokenizer.decode(gen_ids, skip_special_tokens=True)

        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(loop.run_in_executor(None, _sync_generate), timeout=timeout_sec)
