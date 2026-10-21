"""
Parishad Review Specialist — Dataset Validation Suite.
Validates all JSONL files against schema constraints, finding preservation rules,
valid enum values, evidence traceability, and JSON formatting.
Includes data leakage checks between train, validation, and test splits.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

VALID_STATUSES = {"blocking", "needs_review", "no_known_conflict", "insufficient_evidence"}
VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
VALID_CATEGORIES = {
    "policy_conflict",
    "unit_mismatch",
    "tolerance_drift",
    "dependency_risk",
    "unverified_claim",
    "security_threat",
    "low_severity_improvement",
}
VALID_IMPROVEMENT_TYPES = {"required_fix", "optional_improvement", "verification_needed"}
VALID_CONFIDENCES = {"high", "medium", "low"}


class ValidationError:
    def __init__(self, record_id: str, field: str, message: str):
        self.record_id = record_id
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"[{self.record_id}] {self.field}: {self.message}"


def validate_report(report: dict[str, Any], input_finding_ids: list[str], record_id: str) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if report.get("schema_version") != "1.0":
        errors.append(ValidationError(record_id, "schema_version", f"Expected '1.0', got '{report.get('schema_version')}'"))

    if not report.get("review_title"):
        errors.append(ValidationError(record_id, "review_title", "Missing or empty review_title"))

    status = report.get("overall_status")
    if status not in VALID_STATUSES:
        errors.append(ValidationError(record_id, "overall_status", f"Invalid status '{status}', must be in {VALID_STATUSES}"))

    if not report.get("executive_summary"):
        errors.append(ValidationError(record_id, "executive_summary", "Missing or empty executive_summary"))

    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append(ValidationError(record_id, "findings", "findings must be a list"))
        return errors

    seen_finding_ids: set[str] = set()
    output_finding_ids: list[str] = []

    for idx, f in enumerate(findings):
        f_prefix = f"findings[{idx}]"
        f_id = f.get("finding_id")
        if not f_id:
            errors.append(ValidationError(record_id, f"{f_prefix}.finding_id", "Missing finding_id"))
            continue

        if f_id in seen_finding_ids:
            errors.append(ValidationError(record_id, f"{f_prefix}.finding_id", f"Duplicate finding_id '{f_id}'"))
        seen_finding_ids.add(f_id)
        output_finding_ids.append(f_id)

        sev = f.get("severity")
        if sev not in VALID_SEVERITIES:
            errors.append(ValidationError(record_id, f"{f_prefix}.severity", f"Invalid severity '{sev}'"))

        cat = f.get("category")
        if cat not in VALID_CATEGORIES:
            errors.append(ValidationError(record_id, f"{f_prefix}.category", f"Invalid category '{cat}'"))

        conf = f.get("confidence")
        if conf not in VALID_CONFIDENCES:
            errors.append(ValidationError(record_id, f"{f_prefix}.confidence", f"Invalid confidence '{conf}'"))

        evidence = f.get("evidence", [])
        if not isinstance(evidence, list):
            errors.append(ValidationError(record_id, f"{f_prefix}.evidence", "evidence must be a list"))
        elif (sev in ("critical", "high") or cat == "policy_conflict") and len(evidence) == 0:
            errors.append(ValidationError(record_id, f"{f_prefix}.evidence", "Critical/high/policy findings must have evidence"))
        else:
            for ev_idx, ev in enumerate(evidence):
                if not ev.get("source"):
                    errors.append(ValidationError(record_id, f"{f_prefix}.evidence[{ev_idx}]", "Evidence missing source"))

    # Completeness verification: all input finding IDs preserved
    missing_ids = [fid for fid in input_finding_ids if fid not in seen_finding_ids]
    if missing_ids:
        errors.append(ValidationError(record_id, "completeness", f"Missing input finding IDs: {missing_ids}"))

    # Validate completeness object if present
    comp = report.get("completeness")
    if comp:
        if comp.get("all_input_findings_preserved") is not True and len(missing_ids) == 0:
            errors.append(ValidationError(record_id, "completeness.all_input_findings_preserved", "Should be True"))
        if comp.get("input_finding_count") != len(input_finding_ids):
            errors.append(ValidationError(record_id, "completeness.input_finding_count", f"Mismatch: expected {len(input_finding_ids)}, got {comp.get('input_finding_count')}"))

    # Validate improvements
    improvements = report.get("improvements", [])
    for imp_idx, imp in enumerate(improvements):
        imp_prefix = f"improvements[{imp_idx}]"
        if not imp.get("title"):
            errors.append(ValidationError(record_id, f"{imp_prefix}.title", "Missing improvement title"))
        imp_type = imp.get("type")
        if imp_type not in VALID_IMPROVEMENT_TYPES:
            errors.append(ValidationError(record_id, f"{imp_prefix}.type", f"Invalid improvement type '{imp_type}'"))

    return errors


def validate_file(jsonl_path: Path) -> tuple[int, list[ValidationError], list[str]]:
    errors: list[ValidationError] = []
    record_count = 0
    user_prompts: list[str] = []

    if not jsonl_path.exists():
        return 0, [ValidationError(str(jsonl_path), "file", "File does not exist")], []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            record_count += 1
            try:
                rec = json.loads(line)
            except Exception as e:
                errors.append(ValidationError(f"line_{line_no}", "json", f"JSON parse error: {e}"))
                continue

            rec_id = rec.get("example_id", f"line_{line_no}")
            for req_key in ("example_id", "category", "source_type", "scenario_family", "validation_status", "messages"):
                if req_key not in rec:
                    errors.append(ValidationError(rec_id, req_key, f"Missing required top-level key '{req_key}'"))

            if rec.get("validation_status") != "validated":
                errors.append(ValidationError(rec_id, "validation_status", "Expected validation_status='validated'"))

            messages = rec.get("messages", [])
            if len(messages) != 3:
                errors.append(ValidationError(rec_id, "messages", f"Expected 3 messages, got {len(messages)}"))
                continue

            user_msg = messages[1].get("content", "")
            assistant_msg = messages[2].get("content", "")
            user_prompts.append(user_msg)

            try:
                report = json.loads(assistant_msg)
            except Exception as e:
                errors.append(ValidationError(rec_id, "assistant_message", f"Assistant message is not valid JSON: {e}"))
                continue

            input_fids = rec.get("input_finding_ids", [])
            report_errors = validate_report(report, input_fids, rec_id)
            errors.extend(report_errors)

    return record_count, errors, user_prompts


def main():
    data_dir = Path(__file__).resolve().parent
    files = ["train.jsonl", "validation.jsonl", "test.jsonl", "challenge_cases.jsonl"]

    total_records = 0
    total_errors = 0
    prompts_by_split: dict[str, list[str]] = {}

    print("==================================================")
    print("  PARISHAD REVIEW DATASET VALIDATION REPORT")
    print("==================================================")

    for fname in files:
        fpath = data_dir / fname
        count, errs, prompts = validate_file(fpath)
        total_records += count
        total_errors += len(errs)
        prompts_by_split[fname] = prompts

        status_str = "PASSED (0 errors)" if len(errs) == 0 else f"FAILED ({len(errs)} errors)"
        print(f"File: {fname:25s} | Records: {count:4d} | Status: {status_str}")

        if errs:
            for err in errs[:10]:
                print(f"   ERROR: {err}")
            if len(errs) > 10:
                print(f"   ... and {len(errs) - 10} more errors")

    # Leakage check: Ensure no prompt from test or validation is in train
    train_prompts = set(prompts_by_split.get("train.jsonl", []))
    val_prompts = set(prompts_by_split.get("validation.jsonl", []))
    test_prompts = set(prompts_by_split.get("test.jsonl", []))

    val_leaks = val_prompts.intersection(train_prompts)
    test_leaks = test_prompts.intersection(train_prompts)

    if val_leaks:
        print(f"ERROR: {len(val_leaks)} validation records leak directly into train.jsonl!")
        total_errors += len(val_leaks)
    else:
        print("Data Leakage Check (Val vs Train): PASSED (0 overlapping prompts)")

    if test_leaks:
        print(f"ERROR: {len(test_leaks)} test records leak directly into train.jsonl!")
        total_errors += len(test_leaks)
    else:
        print("Data Leakage Check (Test vs Train): PASSED (0 overlapping prompts)")

    print("--------------------------------------------------")
    print(f"Total Records Validated: {total_records}")
    print(f"Total Validation Errors: {total_errors}")

    if total_errors == 0:
        print("ALL DATASET FILES PASSED VALIDATION WITH ZERO DEFECTS.")
        sys.exit(0)
    else:
        print("DATASET VALIDATION FAILED. Please review the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
