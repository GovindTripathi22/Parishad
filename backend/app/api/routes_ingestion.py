"""
CortexOS — Ingestion Webhooks & Events
Receive webhook payloads from GitHub, Slack, Jira, and Google Drive.
Parse, persist as IngestionEvents, and run signal-vs-noise filtering.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import get_db
from app.database.models import IngestionEvent
from app.extraction.signal_filter import classify_signal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingestion"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class WebhookResponse(BaseModel):
    status: str = "received"
    event_id: Optional[str] = None
    source_type: str
    message: str = ""


class EventOut(BaseModel):
    id: str
    source_type: str
    source_id: Optional[str] = None
    raw_content: str
    clean_content: Optional[str] = None
    is_signal: bool = False
    signal_type: Optional[str] = None
    created_at: Optional[str] = None


class ProcessResult(BaseModel):
    event_id: str
    is_signal: bool
    signal_type: Optional[str] = None
    confidence: float = 0.0
    check_triggered: bool = False
    message: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _save_event(
    db: AsyncSession,
    source_type: str,
    source_id: Optional[str],
    raw_content: str,
    raw_payload: Optional[dict[str, Any]] = None,
) -> IngestionEvent:
    """Persist an ingestion event to the database."""
    event = IngestionEvent(
        source_type=source_type,
        source_id=source_id,
        raw_content=raw_content,
        clean_content=raw_content,
        metadata=raw_payload,
        is_signal=False,
        signal_type=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


def _verify_github_signature(payload_body: bytes, signature: Optional[str], secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Webhook Receivers ─────────────────────────────────────────────────────────

@router.post(
    "/api/v1/webhooks/github",
    response_model=WebhookResponse,
    summary="Receive GitHub webhook",
)
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    """Receive and process a GitHub webhook event."""
    payload_bytes = await request.body()
    settings = get_settings()

    if settings.GITHUB_WEBHOOK_SECRET:
        if not _verify_github_signature(payload_bytes, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    event_type = x_github_event or payload.get("action", "unknown")
    repo = payload.get("repository", {}).get("full_name", "unknown")
    source_id = str(payload.get("number") or payload.get("pull_request", {}).get("number") or repo)

    summary = f"[GitHub] {event_type} on {repo}"
    if "pull_request" in payload:
        pr = payload["pull_request"]
        title = pr.get("title", "")
        body = pr.get("body", "")
        summary = f"PR #{pr.get('number')} {event_type}: {title}. {body}"

    event = await _save_event(db, "github", source_id, summary, payload)
    return WebhookResponse(status="received", event_id=event.id, source_type="github", message=summary[:140])


@router.post(
    "/api/v1/webhooks/slack",
    summary="Receive Slack event",
)
async def slack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Slack Events API payloads including URL verification challenge."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event_data = payload.get("event", {})
    channel = event_data.get("channel", "general")
    user = event_data.get("user", "user")
    text = event_data.get("text", "")
    source_id = f"{channel}:{event_data.get('ts', '')}"

    summary = f"[Slack] #{channel} from @{user}: {text}"
    event = await _save_event(db, "slack", source_id, summary, payload)
    return WebhookResponse(status="received", event_id=event.id, source_type="slack", message=summary[:140])


@router.post(
    "/api/v1/webhooks/jira",
    response_model=WebhookResponse,
    summary="Receive Jira webhook",
)
async def jira_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive and process a Jira issue update or resolution."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    issue = payload.get("issue", {})
    key = issue.get("key", "UNKNOWN")
    summary_text = issue.get("fields", {}).get("summary", "")
    desc = issue.get("fields", {}).get("description", "")
    event_type = payload.get("webhookEvent", "issue_updated")

    summary = f"[Jira {key}] {event_type}: {summary_text}. {desc}"
    event = await _save_event(db, "jira", key, summary, payload)
    return WebhookResponse(status="received", event_id=event.id, source_type="jira", message=summary[:140])


@router.post(
    "/api/v1/webhooks/gdrive",
    response_model=WebhookResponse,
    summary="Receive Google Drive push notification",
)
async def gdrive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_goog_channel_id: Optional[str] = Header(None),
    x_goog_resource_state: Optional[str] = Header(None),
):
    """Receive Google Drive push notification."""
    try:
        payload_bytes = await request.body()
        payload = json.loads(payload_bytes) if payload_bytes else {}
    except Exception:
        payload = {}

    state = x_goog_resource_state or "change"
    channel = x_goog_channel_id or "gdrive_channel"
    summary = f"[Google Drive] Resource state '{state}' on channel {channel}"

    event = await _save_event(db, "gdrive", channel, summary, payload)
    return WebhookResponse(status="received", event_id=event.id, source_type="gdrive", message=summary)


@router.get(
    "/api/v1/ingestion/events",
    response_model=list[EventOut],
    summary="List recent ingestion events",
)
async def list_events(
    source_type: Optional[str] = Query(None),
    is_signal: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Query recent external events captured from GitHub, Slack, Jira, or GDrive."""
    stmt = select(IngestionEvent).order_by(IngestionEvent.created_at.desc()).limit(limit)
    if source_type:
        stmt = stmt.where(IngestionEvent.source_type == source_type)
    if is_signal is not None:
        stmt = stmt.where(IngestionEvent.is_signal == is_signal)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        EventOut(
            id=r.id,
            source_type=r.source_type,
            source_id=r.source_id,
            raw_content=r.raw_content,
            clean_content=r.clean_content,
            is_signal=r.is_signal,
            signal_type=r.signal_type,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post(
    "/api/v1/ingestion/process/{event_id}",
    response_model=ProcessResult,
    summary="Run signal filtering on an ingestion event",
)
async def process_event(event_id: str, db: AsyncSession = Depends(get_db)):
    """
    Applies the LLM Signal-vs-Noise filter. If classified as a durable decision,
    standard operating procedure, or architectural change, automatically triggers
    the CortexOS check pipeline.
    """
    stmt = select(IngestionEvent).where(IngestionEvent.id == event_id)
    event = (await db.execute(stmt)).scalars().first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion event not found")

    signal = await classify_signal(event.raw_content, source=event.source_type)

    event.is_signal = signal.is_signal
    event.signal_type = signal.signal_type
    event.processed_at = datetime.now(timezone.utc)

    check_triggered = False
    if signal.is_signal:
        try:
            from app.api.routes_check import CheckRequest, check_update
            req = CheckRequest(
                source=f"{event.source_type.title()} Ingestion",
                topic=signal.signal_type or "AUTO",
                message=event.raw_content,
                enforce_privacy=True,
            )
            await check_update(req, db)
            check_triggered = True
        except Exception as exc:
            logger.warning("Pipeline trigger after ingestion failed: %s", exc)

    await db.commit()

    return ProcessResult(
        event_id=event.id,
        is_signal=signal.is_signal,
        signal_type=signal.signal_type,
        confidence=signal.confidence,
        check_triggered=check_triggered,
        message="Signal verified — check pipeline executed" if check_triggered else "Processed as noise/casual chatter",
    )
