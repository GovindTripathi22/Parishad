"""
Parishad Review Specialist — Review Summary Service.
Orchestrates model invocation, strict schema validation, independent backend
completeness auditing, and deterministic fallback generation.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.specialist.schemas import (
    FindingEvidence,
    ParishadReviewInput,
    ParishadReviewReport,
    ReviewCompleteness,
    ReviewFinding,
    ReviewImprovement,
)
from app.specialist.provider import LocalReviewSummaryProvider, ModelUnavailableError

logger = logging.getLogger(__name__)


class ReviewSummaryService:
    """
    Unified service for producing structured Parishad Review Reports.
    Enforces non-negotiable correctness rules and 100% finding preservation.
    """

    def __init__(self, provider: Optional[LocalReviewSummaryProvider] = None) -> None:
        self.provider = provider or LocalReviewSummaryProvider.get_instance()

    async def generate_review_summary(self, review_input: ParishadReviewInput) -> ParishadReviewReport:
        """
        Primary entry point.
        Attempts local model inference when enabled; independently verifies finding completeness;
        gracefully falls back to deterministic synthesis if model fails, times out, or omits findings.
        """
        input_fids = [f.get("finding_id") for f in review_input.pipeline_findings if f.get("finding_id")]

        if self.provider.is_enabled:
            try:
                user_prompt = self.provider.build_prompt(
                    change_request=review_input.change_request,
                    source=review_input.source,
                    topic=review_input.topic,
                    official_docs=review_input.official_docs,
                    pipeline_findings=review_input.pipeline_findings,
                    ledger_history=review_input.ledger_history,
                    missing_context=review_input.missing_context,
                    security_warnings=review_input.security_warnings,
                )

                raw_output = await self.provider.generate_raw(user_prompt)
                if raw_output:
                    parsed_json = self.provider.extract_json(raw_output)
                    if parsed_json:
                        report = self._validate_and_audit(parsed_json, input_fids)
                        if report is not None:
                            logger.info("Successfully generated review report via fine-tuned model.")
                            return report
                        else:
                            logger.warning("Model output failed finding completeness audit. Triggering fallback.")
                    else:
                        logger.warning("Model output could not be parsed as valid JSON. Triggering fallback.")
            except ModelUnavailableError as exc:
                logger.info("Local model unavailable (%s). Using deterministic fallback.", exc)
            except Exception as exc:
                logger.warning("Local specialist inference failed: %s. Using deterministic fallback.", exc)

        # Fallback path (default when local model disabled, missing, or fails)
        return self.generate_deterministic_fallback(review_input)

    def _validate_and_audit(
        self, parsed_json: dict[str, Any], input_fids: list[str]
    ) -> Optional[ParishadReviewReport]:
        """
        Validates the Pydantic schema and independently audits finding-ID completeness.
        Returns None if any input finding ID was dropped by the model.
        """
        try:
            report = ParishadReviewReport.model_validate(parsed_json)
        except Exception as exc:
            logger.warning("Schema validation error on model output: %s", exc)
            return None

        output_fids = [f.finding_id for f in report.findings]
        seen_fids: set[str] = set()
        duplicates: list[str] = []
        for fid in output_fids:
            if fid in seen_fids:
                duplicates.append(fid)
            seen_fids.add(fid)

        missing = [fid for fid in input_fids if fid not in seen_fids]

        # Non-negotiable rule 1 & 2: All input findings MUST be preserved
        if missing or duplicates:
            logger.warning("Completeness check failed: missing=%s, duplicates=%s", missing, duplicates)
            return None

        # Recompute completeness independently in backend code
        report.completeness = ReviewCompleteness(
            input_finding_count=len(input_fids),
            output_finding_count=len(output_fids),
            missing_finding_ids=[],
            duplicate_finding_ids=[],
            all_input_findings_preserved=True,
        )
        report.fallback_used = False
        return report

    def generate_deterministic_fallback(self, review_input: ParishadReviewInput) -> ParishadReviewReport:
        """
        Deterministic Fallback Formatter.
        Constructs a complete, 100% compliant review report adhering to all Parishad rules,
        preserving every single finding and citing official documents.
        """
        findings: list[ReviewFinding] = []
        improvements: list[ReviewImprovement] = []
        has_blocking = False

        top_doc = review_input.official_docs[0] if review_input.official_docs else {}
        top_doc_id = top_doc.get("doc_id", "DOC-SPECS")
        top_doc_title = top_doc.get("title", "Official Specification")

        for f_dict in review_input.pipeline_findings:
            f_id = f_dict.get("finding_id", "FIND-UNKNOWN")
            cat_raw = str(f_dict.get("category", "policy_conflict")).lower()
            if cat_raw in ("policy_conflict", "unit_mismatch", "tolerance_drift", "dependency_risk", "unverified_claim", "security_threat", "low_severity_improvement"):
                cat = cat_raw
            elif "security" in cat_raw or "threat" in cat_raw or "injection" in cat_raw:
                cat = "security_threat"
            elif "unit" in cat_raw:
                cat = "unit_mismatch"
            elif "dep" in cat_raw:
                cat = "dependency_risk"
            elif "unverif" in cat_raw or "insufficient" in cat_raw:
                cat = "unverified_claim"
            elif "improv" in cat_raw or "info" in cat_raw:
                cat = "low_severity_improvement"
            else:
                cat = "policy_conflict"

            prop_val = f_dict.get("proposed_value", "Proposed")
            off_val = f_dict.get("official_value", "Certified")
            delta = f_dict.get("delta_pct", 0.0)
            target_doc = f_dict.get("target_doc_id", top_doc_id)
            initial_rat = f_dict.get("initial_rationale", "")

            # Respect upstream severity if valid; otherwise derive from delta
            sev_raw = str(f_dict.get("severity", "")).lower()
            if sev_raw in ("critical", "high", "medium", "low", "info"):
                sev = sev_raw
            elif cat == "security_threat":
                sev = "critical"
            elif delta > 50.0:
                sev = "critical"
            elif delta > 20.0:
                sev = "high"
            elif delta > 5.0:
                sev = "medium"
            else:
                sev = "low"

            if sev in ("critical", "high"):
                has_blocking = True

            title = f_dict.get("title") or f"Parameter Drift in {target_doc}: {prop_val} vs {off_val}"
            desc = f_dict.get("description") or initial_rat or (
                f"Proposed parameter {prop_val} deviates from official {off_val} in {target_doc} ({delta}% drift)."
            )
            impact = f_dict.get("impact") or f"Operational shift of {delta}% against certified baseline rules."
            rec_fix = f_dict.get("recommended_fix") or f"Retain certified baseline {off_val} or stage formal approval for {prop_val}."

            evidence_items: list[FindingEvidence] = []
            if f_dict.get("evidence"):
                for ev in f_dict["evidence"]:
                    if isinstance(ev, dict) and ev.get("source"):
                        evidence_items.append(
                            FindingEvidence(
                                source=ev.get("source", target_doc),
                                quote=ev.get("quote"),
                                location=ev.get("location"),
                            )
                        )
                    elif isinstance(ev, FindingEvidence):
                        evidence_items.append(ev)
            if not evidence_items:
                if top_doc.get("content"):
                    evidence_items.append(
                        FindingEvidence(
                            source=target_doc,
                            quote=top_doc.get("content", "")[:140] + ("..." if len(top_doc.get("content", "")) > 140 else ""),
                            location="Certified Specification",
                        )
                    )
                else:
                    evidence_items.append(FindingEvidence(source=target_doc, location="Baseline Rule"))

            rf = ReviewFinding(
                finding_id=f_id,
                category=cat,
                severity=sev,
                title=title,
                description=desc,
                evidence=evidence_items,
                impact=impact,
                recommended_fix=rec_fix,
                confidence="high",
                is_inference=bool(f_dict.get("is_inference", False)),
                needs_human_verification=bool(f_dict.get("needs_human_verification", True)),
            )
            findings.append(rf)

            improvements.append(
                ReviewImprovement(
                    title=f"Resolve Parameter Drift for {f_id}",
                    description=f"Verify hardware or policy tolerance for {prop_val} change before approval.",
                    linked_finding_ids=[f_id],
                    type="required_fix" if sev in ("critical", "high") else "optional_improvement",
                )
            )

        # If security warnings are present but no findings existed, generate a security finding
        if review_input.security_warnings and not any(f.category == "security_threat" for f in findings):
            sec_desc = "; ".join(review_input.security_warnings)
            rf_sec = ReviewFinding(
                finding_id="FIND-SEC-01",
                category="security_threat",
                severity="critical",
                title="Security Alert Flagged by Guard",
                description=f"Automated security guard detected adversarial patterns: {sec_desc}",
                evidence=[FindingEvidence(source="CortexOS Security Guard", location="Input Inspection")],
                impact="Potential prompt injection or safety rule override attempt.",
                recommended_fix="Block update and conduct security review of submission origin.",
                confidence="high",
                is_inference=False,
                needs_human_verification=True,
            )
            findings.append(rf_sec)
            improvements.append(
                ReviewImprovement(
                    title="Security Remediation",
                    description="Sanitize input payload and investigate suspicious directives.",
                    linked_finding_ids=["FIND-SEC-01"],
                    type="required_fix",
                )
            )

        # Status determination
        if review_input.security_warnings or any(f.category == "security_threat" for f in findings):
            overall_status = "blocking"
            review_title = "Security Alert & Rule Violation Review"
            summary = "The submission contains security alerts or hazardous parameter violations requiring immediate intervention."
        elif has_blocking or any(f.severity in ("critical", "high") for f in findings):
            overall_status = "blocking"
            review_title = f"Blocking Policy Conflicts in {top_doc_id}"
            summary = (
                f"Proposed change introduces {len(findings)} finding(s) that conflict with certified specifications in {top_doc_title}. "
                "Human approval is required before changes can be applied."
            )
        elif findings:
            overall_status = "needs_review"
            review_title = f"Review Required for {top_doc_id}"
            summary = f"Identified {len(findings)} parameter change(s) requiring verification against certified tolerances in {top_doc_title}."
        elif review_input.missing_context:
            overall_status = "insufficient_evidence"
            review_title = "Insufficient Evidence for Verification"
            summary = "The proposal lacks required parameter specifications or test metrics to verify against official rules."
        else:
            overall_status = "no_known_conflict"
            review_title = f"Verified Compliant with {top_doc_id}"
            summary = f"The proposed change conforms with certified specifications in {top_doc_title} ({top_doc_id}). Zero conflicts detected."

        input_fids = [f.get("finding_id") for f in review_input.pipeline_findings if f.get("finding_id")]

        completeness = ReviewCompleteness(
            input_finding_count=len(input_fids),
            output_finding_count=len(findings),
            missing_finding_ids=[],
            duplicate_finding_ids=[],
            all_input_findings_preserved=True,
        )

        return ParishadReviewReport(
            schema_version="1.0",
            review_title=review_title,
            overall_status=overall_status,
            executive_summary=summary,
            findings=findings,
            missing_information=review_input.missing_context,
            improvements=improvements,
            assumptions=["Generated via deterministic fallback formatter preserving all input findings."],
            questions_for_reviewer=[
                f"Has the engineering team reviewed the proposed update against {top_doc_id}?"
            ] if findings else [],
            completeness=completeness,
            fallback_used=True,
        )
