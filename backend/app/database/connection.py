"""
CortexOS Database Connection — Async SQLAlchemy session management & startup seeding.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import select

from app.config import get_settings
from app.database.models import (
    Base, OfficialDoc, DocRule, PendingApproval,
    DecisionLedgerEntry, VectorChunk,
)

logger = logging.getLogger("cortexos.db")

_settings = get_settings()

engine = create_async_engine(
    _settings.DATABASE_URL,
    echo=_settings.DATABASE_ECHO,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables and seed initial data if empty. Safely adds any missing columns."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Schema evolution helper for SQLite
        try:
            from sqlalchemy import text
            # Check decision_ledger columns
            res = await conn.execute(text("PRAGMA table_info(decision_ledger)"))
            columns = [row[1] for row in res.fetchall()]
            if "prev_hash" not in columns:
                await conn.execute(text(f"ALTER TABLE decision_ledger ADD COLUMN prev_hash VARCHAR(128) DEFAULT '{'0'*64}'"))
            if "doc_version" not in columns:
                await conn.execute(text("ALTER TABLE decision_ledger ADD COLUMN doc_version VARCHAR(32) DEFAULT 'v1.0'"))
            if "canonical_payload" not in columns:
                await conn.execute(text("ALTER TABLE decision_ledger ADD COLUMN canonical_payload TEXT"))

            # Check pending_approvals columns
            res_p = await conn.execute(text("PRAGMA table_info(pending_approvals)"))
            p_cols = [row[1] for row in res_p.fetchall()]
            if "target_doc_version" not in p_cols:
                await conn.execute(text("ALTER TABLE pending_approvals ADD COLUMN target_doc_version VARCHAR(32) DEFAULT 'v1.0'"))
        except Exception as exc:
            logger.warning("Column migration check notice: %s", exc)

    logger.info("Database tables created/verified.")

    async with async_session_factory() as session:
        result = await session.execute(select(OfficialDoc).limit(1))
        if result.scalars().first() is None:
            await _seed_initial_data(session)
            await session.commit()
            logger.info("Seeded initial company documents.")


async def _seed_initial_data(session: AsyncSession) -> None:
    """Seed the database with realistic starter documents and decision history."""

    # ── Official Documents ────────────────────────────────────
    docs_data = [
        {
            "doc_id": "DOC-BMS-402",
            "topic": "BMS-48V",
            "title": "48V Smart Battery Sensor Electrical & Thermal Specification",
            "version": "v2.4",
            "content": "Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125°C.",
            "source": "engineering-spec",
            "rules": [
                {"label": "Max Voltage Limit", "value": 54.0, "unit": "v"},
                {"label": "Thermal Shutdown Limit", "value": 125.0, "unit": "c"},
            ],
        },
        {
            "doc_id": "DOC-RAD-118",
            "topic": "RADAR-77GHZ",
            "title": "77GHz Corner Radar CAN-FD Communication Specification",
            "version": "v3.1",
            "content": "Primary bus uses CAN-FD. Approved data phase speed is 2.0 Mbps on standard twisted-pair harnesses to guarantee electromagnetic safety.",
            "source": "engineering-spec",
            "rules": [
                {"label": "CAN-FD Data Speed", "value": 2.0, "unit": "mbps"},
            ],
        },
        {
            "doc_id": "DOC-POLICY-101",
            "topic": "COMPANY-OPS",
            "title": "Customer Refund & Support SLA Policy",
            "version": "v1.8",
            "content": "Customers are guaranteed a full self-serve refund within 30 days of purchase. Critical support tickets must receive a response within 4 hours.",
            "source": "policy-doc",
            "rules": [
                {"label": "Refund Window", "value": 30, "unit": "days"},
                {"label": "Support SLA Response", "value": 4, "unit": "hours"},
            ],
        },
        {
            "doc_id": "DOC-CLOUD-305",
            "topic": "CLOUD-INFRA",
            "title": "Cloud Infrastructure Latency & Budget Policy",
            "version": "v1.2",
            "content": "Production API gateway response latency must stay under 200 ms. Maximum approved monthly cluster budget is $5000.",
            "source": "infrastructure-policy",
            "rules": [
                {"label": "Max API Latency", "value": 200, "unit": "ms"},
                {"label": "Monthly Cloud Budget", "value": 5000, "unit": "usd"},
            ],
        },
        {
            "doc_id": "DOC-ARCH-200",
            "topic": "ARCHITECTURE",
            "title": "Service Architecture Decision Record",
            "version": "v1.0",
            "content": "All new services must be containerized with Docker. Maximum memory allocation per pod is 2 GB. Inter-service communication uses gRPC with 50 ms timeout.",
            "source": "architecture-adr",
            "rules": [
                {"label": "Max Pod Memory", "value": 2, "unit": "gb"},
                {"label": "gRPC Timeout", "value": 50, "unit": "ms"},
            ],
        },
    ]

    for d in docs_data:
        doc = OfficialDoc(
            doc_id=d["doc_id"],
            topic=d["topic"],
            title=d["title"],
            version=d["version"],
            content=d["content"],
            source=d["source"],
        )
        session.add(doc)
        for r in d["rules"]:
            rule = DocRule(doc_id=d["doc_id"], label=r["label"], value=r["value"], unit=r["unit"])
            session.add(rule)

    # ── Pre-seeded pending approvals ──────────────────────────
    approvals = [
        PendingApproval(
            draft_id="REV-101",
            source="GitHub Pull Request (#418)",
            topic="BMS-48V",
            target_doc_id="DOC-BMS-402",
            parameter_label="Max Voltage Limit",
            unit="v",
            old_value="54 V",
            new_value="60 V",
            numeric_new=60.0,
            reason="Pull Request #418 raises the 48V Battery Sensor overvoltage limit from 54 V to 60 V (+11.1% change). Official spec DOC-BMS-402 caps this at 54 V.",
            old_text="Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 54.0V to prevent MOSFET breakdown. Thermal shutdown limit is 125°C.",
            proposed_text="Nominal operating voltage is 48.0V. Maximum transient overvoltage threshold is strictly 60.0V (updated per PR #418 cold-crank braking requirement). Thermal shutdown limit is 125°C.",
            status="PENDING",
        ),
        PendingApproval(
            draft_id="REV-102",
            source="Slack Team Chat (#product-ops)",
            topic="COMPANY-OPS",
            target_doc_id="DOC-POLICY-101",
            parameter_label="Refund Window",
            unit="days",
            old_value="30 days",
            new_value="14 days",
            numeric_new=14,
            reason="Team chat proposes shortening the customer refund window from 30 days to 14 days (-53.3% change), contradicting DOC-POLICY-101.",
            old_text="Customers are guaranteed a full self-serve refund within 30 days of purchase. Critical support tickets must receive a response within 4 hours.",
            proposed_text="Customers are guaranteed a full self-serve refund within 14 days of purchase. Critical support tickets must receive a response within 4 hours.",
            status="PENDING",
        ),
    ]
    for a in approvals:
        session.add(a)

    # ── Decision Ledger seed (Cryptographically Chained with Full SHA-256) ──
    genesis_prev = "0" * 64
    d1_payload = {
        "decision_id": "DEC-2026-01",
        "doc_id": "DOC-BMS-402",
        "doc_version": "v2.4",
        "parameter": "Max Voltage Limit",
        "old_value": "52 V",
        "new_value": "54 V",
        "status": "APPROVED",
        "timestamp": "2026-09-12T00:00:00Z",
    }
    canon_d1 = json.dumps(d1_payload, sort_keys=True)
    hash_d1 = "sha256:" + hashlib.sha256((canon_d1 + genesis_prev).encode("utf-8")).hexdigest()

    d2_payload = {
        "decision_id": "DEC-2026-02",
        "doc_id": "DOC-POLICY-101",
        "doc_version": "v1.8",
        "parameter": "Refund Window",
        "old_value": "15 days",
        "new_value": "30 days",
        "status": "APPROVED",
        "timestamp": "2026-10-01T00:00:00Z",
    }
    canon_d2 = json.dumps(d2_payload, sort_keys=True)
    hash_d2 = "sha256:" + hashlib.sha256((canon_d2 + hash_d1).encode("utf-8")).hexdigest()

    ledger = [
        DecisionLedgerEntry(
            decision_id="DEC-2026-01",
            timestamp=datetime(2026, 9, 12, tzinfo=timezone.utc),
            topic="BMS-48V",
            parameter="Max Voltage Limit",
            old_value="52 V",
            new_value="54 V",
            status="APPROVED",
            doc_id="DOC-BMS-402",
            doc_version="v2.4",
            note="Validated 54V clamp limit in thermal chamber test.",
            prev_hash=genesis_prev,
            canonical_payload=canon_d1,
            hash=hash_d1,
            reviewed_by="lead_auditor",
        ),
        DecisionLedgerEntry(
            decision_id="DEC-2026-02",
            timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
            topic="COMPANY-OPS",
            parameter="Refund Window",
            old_value="15 days",
            new_value="30 days",
            status="APPROVED",
            doc_id="DOC-POLICY-101",
            doc_version="v1.8",
            note="Standardized 30-day customer refund policy for Q4.",
            prev_hash=hash_d1,
            canonical_payload=canon_d2,
            hash=hash_d2,
            reviewed_by="ops_governance",
        ),
    ]
    for entry in ledger:
        session.add(entry)
