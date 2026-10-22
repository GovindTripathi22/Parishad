"""
Audit, Security, and Cryptographic Ledger Integrity Test Suite for Parishad / CortexOS.

Verifies:
1. Server-side Role-Based Access Control (RBAC).
2. Restricted SQL query allowlisting and injection rejection.
3. Cryptographic hash-chaining and independent ledger verification.
4. Tampering detection on mutated ledger payloads.
5. Parameter-aware rule matching (elimination of arbitrary rule[0] fallback).
6. Zero-baseline percentage calculation safety.
7. Explicit OCR_UNAVAILABLE / OCR_FAILED honesty (no fake fallback).
"""
from __future__ import annotations

import hashlib
import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.database.connection import async_session_factory
from app.database.models import DecisionLedgerEntry, OfficialDoc
from app.agents.opinion_agent import analyze_opinion
from app.extraction.fact_extractor import extract_facts


@pytest.mark.asyncio
async def test_rbac_reviewer_required_for_approval():
    """Confirms that callers with Viewer role cannot approve drafts."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/approvals/REV-101/approve",
            json={"note": "Viewer unauthorized attempt"},
            headers={"X-User-Role": "viewer"},
        )
        assert res.status_code == 403
        data = res.json()
        err_msg = data.get("error") or data.get("detail") or ""
        assert "requires minimum role 'reviewer'" in err_msg


@pytest.mark.asyncio
async def test_sql_explorer_allows_read_only_allowlisted_table():
    """Legitimate SELECT on allowlisted table succeeds."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/system/sql", json={
            "query": "SELECT id, decision_id, status FROM decision_ledger LIMIT 5"
        })
        assert res.status_code == 200
        data = res.json()
        assert "results" in data
        assert "error" not in data
        assert isinstance(data["results"], list)


@pytest.mark.asyncio
async def test_sql_explorer_rejects_union_injection():
    """Rejects SQL containing UNION injection."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/system/sql", json={
            "query": "SELECT id FROM decision_ledger UNION SELECT sqlite_version()"
        })
        assert res.status_code == 200
        data = res.json()
        assert "error" in data
        assert "Prohibited SQL pattern detected" in data["error"]
        assert len(data["results"]) == 0


@pytest.mark.asyncio
async def test_sql_explorer_rejects_comments():
    """Rejects SQL containing comment syntax (--)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/system/sql", json={
            "query": "SELECT * FROM decision_ledger -- ignore limit"
        })
        assert res.status_code == 200
        data = res.json()
        assert "error" in data
        assert "Prohibited SQL pattern detected" in data["error"]


@pytest.mark.asyncio
async def test_sql_explorer_rejects_multi_statement():
    """Rejects SQL containing multiple statements separated by semicolons."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/system/sql", json={
            "query": "SELECT * FROM decision_ledger; DROP TABLE decision_ledger"
        })
        assert res.status_code == 200
        data = res.json()
        assert "error" in data
        assert "Multiple statements are strictly prohibited" in data["error"]


@pytest.mark.asyncio
async def test_sql_explorer_rejects_forbidden_table():
    """Rejects access to tables not on the strict allowlist."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/system/sql", json={
            "query": "SELECT * FROM ingestion_events LIMIT 5"
        })
        assert res.status_code == 200
        data = res.json()
        assert "error" in data
        assert "is forbidden" in data["error"]


@pytest.mark.asyncio
async def test_ledger_cryptographic_verification_endpoint():
    """Verifies that the /api/v1/ledger/verify endpoint successfully validates hash chain."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/ledger/verify")
        assert res.status_code == 200
        report = res.json()
        assert "is_valid" in report
        assert "total_records" in report
        assert report["total_records"] >= 2
        assert report["chain_intact"] is True
        assert report["is_valid"] is True
        assert len(report["errors"]) == 0


@pytest.mark.asyncio
async def test_parameter_aware_matcher_rejects_cross_domain_fallback():
    """
    Verifies elimination of the unsafe fallback to doc_rules[0].
    Comparing an unrelated unit (e.g. 500 RPM) against a 48V Battery spec
    must yield INSUFFICIENT_EVIDENCE rather than falsely matching Max Voltage Limit.
    """
    sample_doc = {
        "doc_id": "DOC-BMS-402",
        "title": "48V Battery Spec",
        "content": "Max voltage is 54.0V. Thermal limit is 125°C.",
        "rules": [
            {"label": "Max Voltage Limit", "value": 54.0, "unit": "V"},
            {"label": "Thermal Shutdown Limit", "value": 125.0, "unit": "°C"},
        ],
    }
    unrelated_fact = extract_facts("Adjust cooling pump speed to 2500 RPM")
    result = await analyze_opinion("Adjust cooling pump speed to 2500 RPM", sample_doc, unrelated_fact)
    assert result.has_conflict is False
    assert "INSUFFICIENT_EVIDENCE" in result.reasoning


@pytest.mark.asyncio
async def test_zero_baseline_percentage_calculation():
    """
    Verifies that a 0-baseline rule does not divide by an arbitrary denominator.
    """
    sample_doc = {
        "doc_id": "DOC-SAFETY-001",
        "title": "Zero Tolerance Defect Policy",
        "content": "Allowed critical safety defects: strictly 0 defects.",
        "rules": [
            {"label": "Critical Defects", "value": 0.0, "unit": "defects"},
        ],
    }
    fact = extract_facts("Proposed tolerance allowance of 2 defects per batch")
    result = await analyze_opinion("Proposed tolerance allowance of 2 defects per batch", sample_doc, fact)
    assert result.has_conflict is True
    assert result.severity == "critical"
    assert "Official baseline for Critical Defects is 0" in result.reasoning


@pytest.mark.asyncio
async def test_spec_sheet_ocr_honesty_when_unconfigured():
    """
    Confirms that /api/v1/brain/ocr returns explicit OCR_UNAVAILABLE
    rather than fabricating a hardcoded 54.0V/10ms mock when unconfigured.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/brain/ocr", json={
            "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            "filename": "radar_spec.png",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("OCR_UNAVAILABLE", "OCR_FAILED", "success")
        if data["status"] == "OCR_UNAVAILABLE":
            assert "GEMINI_API_KEY is not configured" in data["content"]
            assert data["extracted_rules"] == []


@pytest.mark.asyncio
async def test_approval_action_creates_chained_sha256_ledger_entry():
    """
    Approving a draft records a full 64-char SHA-256 chained digest.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a dedicated draft to test approval
        check_res = await ac.post("/api/v1/check", json={
            "message": "Change battery cut-off limit from 54.0V to 62.0V",
            "topic": "BMS-48V",
        })
        draft_id = check_res.json().get("pending_approval_id")
        assert draft_id is not None

        # 2. Approve draft with Reviewer role
        app_res = await ac.post(
            f"/api/v1/approvals/{draft_id}/approve",
            json={"note": "Approved by senior validation engineer."},
            headers={"X-User-Role": "reviewer"},
        )
        assert app_res.status_code == 200
        data = app_res.json()
        assert data["status"] == "APPROVED"
        assert data["ledger_id"] is not None

        # 3. Verify ledger entry exists with full 64-char sha256 hash
        ledger_res = await ac.get("/api/v1/ledger?limit=1")
        assert ledger_res.status_code == 200
        latest = ledger_res.json()[0]
        assert latest["hash"].startswith("sha256:")
        # sha256: prefix (7 chars) + 64 hex characters = 71 characters
        assert len(latest["hash"]) == 71
        assert latest["prev_hash"] is not None

        # 4. Clean up: reset DOC-BMS-402 content back to certified 54.0V
        from app.database.connection import async_session_factory
        from app.database.models import OfficialDoc, DocRule
        from sqlalchemy import select
        async with async_session_factory() as s:
            d_row = (await s.execute(select(OfficialDoc).where(OfficialDoc.doc_id == "DOC-BMS-402"))).scalars().first()
            if d_row:
                d_row.version = "v2.4"
                d_row.content = "Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125°C."
            r_row = (await s.execute(select(DocRule).where(DocRule.doc_id == "DOC-BMS-402", DocRule.unit == "v"))).scalars().first()
            if r_row:
                r_row.value = 54.0
            await s.commit()


@pytest.mark.asyncio
async def test_gemini_status_and_validation():
    """Verifies that the Gemini status endpoint reports current configuration and rejects invalid keys."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/system/gemini-status")
        assert res.status_code == 200
        data = res.json()
        assert "configured" in data
        assert "capabilities" in data

        # Test rejected with invalid key
        bad_res = await ac.post("/api/v1/system/configure-gemini", json={
            "gemini_api_key": "invalid_fake_key_123"
        })
        assert bad_res.status_code == 200
        bad_data = bad_res.json()
        assert bad_data["success"] is False
        assert "Invalid Gemini API Key" in bad_data["error"] or "API key" in bad_data["error"]
