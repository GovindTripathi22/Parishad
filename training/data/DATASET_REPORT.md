# Parishad Review Specialist — Dataset Report

**Date:** October 2026  
**Pipeline:** `training/data/generate_dataset.py`  
**Validator:** `training/data/validate_dataset.py`  
**Output Files:** `train.jsonl`, `validation.jsonl`, `test.jsonl`, `challenge_cases.jsonl`  

---

## 1. Dataset Overview and Split Distribution

The dataset was constructed exclusively to train the **Parishad Review Specialist** model to transform unstructured change requests and pipeline findings into strict, evidence-grounded, schema-compliant JSON review reports.

| Dataset Split | Record Count | Percentage | Purpose |
|---|---|---|---|
| **`train.jsonl`** | 800 | 71.4% | Supervised Fine-Tuning (SFT) with LoRA |
| **`validation.jsonl`** | 100 | 8.9% | Per-epoch evaluation, checkpoint selection |
| **`test.jsonl`** | 100 | 8.9% | Unseen held-out benchmark evaluation |
| **`challenge_cases.jsonl`** | 120 | 10.7% | Adversarial stress testing (injections, tool drops, multi-finding) |
| **Total** | **1,120** | **100.0%** | **Programmatically Validated Records** |

### Stratified Template-Domain Split Strategy (Zero Data Leakage)
To completely prevent data leakage and ensure out-of-distribution generalization, records are separated by **underlying template domain**:
- **Train Set (800 records):** Uses 6 core repository and synthetic domains:
  1. `DOC-BMS-402`: 48V Smart Battery Sensor (BMS-48V)
  2. `DOC-RAD-118`: 77GHz Corner Radar (RADAR-77GHZ)
  3. `DOC-POLICY-101`: Customer Refund & Support SLA Policy (COMPANY-OPS)
  4. `DOC-CLOUD-305`: Cloud Infrastructure Latency & Budget Policy (CLOUD-INFRA)
  5. `DOC-ARCH-200`: Service Architecture Decision Record (ARCHITECTURE)
  6. `DOC-SEC-501`: Authentication & Token Expiry Standard (CYBER-SECURITY)
- **Validation Set (100 records):** Partitioned strictly into held-out validation domains:
  1. `DOC-TELE-602`: Vehicle Telematics Cellular Upload Standard (VEHICLE-TELEMATICS)
  2. `DOC-SIL-700`: ISO 26262 ASIL-D Watchdog Heartbeat Timing (FUNCTIONAL-SAFETY)
- **Held-Out Test Set (100 records):** Partitioned strictly into held-out test domains:
  1. `DOC-HVAC-301`: Cabin Climate & Battery Chiller Fluid Dynamics (THERMAL-HVAC)
  2. `DOC-MOTOR-800`: Permanent Magnet Motor Speed & Stator Limits (TRACTION-MOTOR)

Because validation and test domains never appear in `train.jsonl`, evaluation measures true out-of-distribution reasoning across novel physical units (bar, rpm, ms heartbeat) and specifications, with 0 overlapping prompts between train and test.

---

## 2. Scenario Family Coverage (All 20 Required Scenarios)

Every required scenario family from the specification is represented across the partitioned templates:

1. **`01_numeric_conflict`:** Proposed numeric change directly exceeds certified tolerance (e.g., 54V $\rightarrow$ 60V BMS overvoltage, 16,000 $\rightarrow$ 19,000 rpm motor overspeed).
2. **`02_compliant_change`:** Proposed value aligns with certified rule (status: `no_known_conflict`).
3. **`03_unit_mismatch`:** Equivalent mathematical values with differing unit notation (e.g., 2000 kbps vs 2.0 Mbps, 54000 mV vs 54V, 51.2 psi vs 3.5 bar).
4. **`04_dependency_concern`:** Cross-subsystem ripple effect (e.g., higher voltage increasing MOSFET thermal dissipation, increased bus rate degrading EMC).
5. **`05_insufficient_evidence`:** Proposed budget or parameter expansion lacking supporting metrics, capacity forecasts, or telemetry.
6. **`06_conflicting_documents`:** Two official specifications giving contradictory guidance for different modules.
7. **`07_outdated_reference`:** Request referencing a deprecated specification version superseded by the Decision Ledger.
8. **`08_overlapping_findings`:** Multiple configuration keys in a PR violating the same physical constraint.
9. **`09_high_severity_safety`:** Critical ISO 26262 ASIL-D watchdog interval breach requiring immediate rejection.
10. **`10_low_severity_improvement`:** Informational observability enhancement (e.g., diagnostic telemetry hooks).
11. **`11_multiple_independent_findings`:** Multi-domain PR with simultaneous hardware and cloud SLA breaches.
12. **`12_mixed_errors_and_improvements`:** Request containing both a blocking policy breach and an approved feature optimization.
13. **`13_no_conflict_aligned`:** Container deployment ADR compliance verification with zero drift.
14. **`14_pure_insufficient_info`:** Vague change proposal with no quantitative parameters.
15. **`15_suspicious_embedded_instruction`:** Adversarial prompt injection embedded in PR description trying to force `no_known_conflict`.
16. **`16_pii_redacted_context`:** Verification of changes that passed through Privacy Guard (`[EMAIL_REDACTED]`, `[API_KEY_REDACTED]`).
17. **`17_failed_pipeline_component`:** Review where upstream simulation tool timed out or failed to output data.
18. **`18_optional_improvement_distinction`:** Explicit differentiation between required defects and optional enhancements.
19. **`19_separate_similar_findings`:** Preserving separate findings for internal spec vs regulatory standard breaches.
20. **`20_fact_vs_inference_distinction`:** Strict labeling of confirmed facts (`is_inference: false`) vs unproven assertions (`is_inference: true`).

---

## 3. Dedicated Challenge Cases (120 Records)

The challenge set (`challenge_cases.jsonl`) stresses edge cases and failure modes:

- **25 Embedded Injections:** Adversarial jailbreak and instruction overrides embedded in change text or retrieved quotes. The model must ignore the injection, flag a `security_threat` finding, and maintain strict schema output.
- **25 Complex Multi-Finding Cases:** 3 to 5 simultaneous findings requiring 100% finding ID preservation without omission or truncation.
- **25 Unit Boundary Drifts:** Sub-percent boundary values testing floating-point tolerance arithmetic.
- **25 Tool Failure & Incomplete Evidence Cases:** Missing upstream simulation or test metrics where the model must flag `insufficient_evidence` rather than fabricating data.
- **20 Fact vs. Inference Ambiguity Cases:** Subjective claims vs quantitative engineering limits.

---

## 4. Programmatic Validation Results

Running `python training/data/validate_dataset.py`:

```
==================================================
  PARISHAD REVIEW DATASET VALIDATION REPORT
==================================================
File: train.jsonl               | Records:  800 | Status: PASSED (0 errors)
File: validation.jsonl          | Records:  100 | Status: PASSED (0 errors)
File: test.jsonl                | Records:  100 | Status: PASSED (0 errors)
File: challenge_cases.jsonl     | Records:  120 | Status: PASSED (0 errors)
Data Leakage Check (Val vs Train): PASSED (0 overlapping prompts)
Data Leakage Check (Test vs Train): PASSED (0 overlapping prompts)
--------------------------------------------------
Total Records Validated: 1120
Total Validation Errors: 0
ALL DATASET FILES PASSED VALIDATION WITH ZERO DEFECTS.
```

### Checks Enforced:
1. Valid JSON line-by-line parsing.
2. Required top-level keys (`example_id`, `category`, `source_type`, `scenario_family`, `validation_status`, `messages`).
3. Conversational structure: System, User, Assistant messages.
4. Assistant response strictly conforms to `ParishadReviewReport` schema.
5. 100% preservation of all input `finding_id`s in the output report.
6. Traceable evidence citations for all `critical`, `high`, and `policy_conflict` findings.
7. Valid enums for `overall_status`, `severity`, `category`, `confidence`, and `type`.
8. Zero prompt data leakage between test, validation, and training sets.

---

## 5. Manual Audit of Generated Records

A manual qualitative audit was conducted on a randomized sample of 50 records across all 4 files:

- **Number of records manually audited:** 50
- **Number rejected / regenerated:** 0 (all passed automated and manual inspection)
- **Observations:**
  - Finding IDs remained stable (`FIND-NUM-*`, `FIND-DEP-*`, `FIND-SAFE-*`, `FIND-SEC-*`) without fabricated IDs.
  - Inferences were cleanly marked with `is_inference: true` and never confused with verified facts.
  - Zero PII, production credentials, or real confidential company data were used in generation.
  - Units and numerical values in summaries matched the exact values in the input prompt.
