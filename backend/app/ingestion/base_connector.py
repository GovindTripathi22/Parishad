"""
CortexOS Ingestion — Base Connector ABC.

Provides the abstract contract that every source connector (GitHub, Slack,
Jira, Google Drive, …) must implement, plus a shared `save_event` helper
that persists an `IngestionPayload` to the database as an `IngestionEvent`.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.database.connection import async_session_factory
from app.database.models import IngestionEvent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Data Models
# ═══════════════════════════════════════════════════════════════

class EventType(str, Enum):
    """Canonical event types emitted by all connectors."""

    PR_MERGED = "pr_merged"
    PR_OPENED = "pr_opened"
    PR_CLOSED = "pr_closed"
    PUSH = "push"
    ISSUE_OPENED = "issue_opened"
    ISSUE_CLOSED = "issue_closed"
    COMMENT = "comment"
    MESSAGE = "message"
    APP_MENTION = "app_mention"
    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    TICKET_RESOLVED = "ticket_resolved"
    DOC_UPDATED = "doc_updated"
    DOC_ADDED = "doc_added"
    DOC_TRASHED = "doc_trashed"
    UNKNOWN = "unknown"


class IngestionPayload(BaseModel):
    """Normalised payload produced by every connector's `parse_event`."""

    source_type: str = Field(
        ...,
        description="Connector origin: github | slack | jira | linear | gdrive",
    )
    source_id: str = Field(
        ...,
        description="Unique resource identifier (PR number, channel ID, ticket key, file ID).",
    )
    raw_content: str = Field(
        ...,
        description="Full raw content / summary text to store.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured metadata (author, labels, etc.).",
    )
    event_type: str = Field(
        default=EventType.UNKNOWN,
        description="Canonical event type.",
    )
    content_hash: str | None = Field(
        default=None,
        description="SHA-256 hash of raw_content for deduplication.",
    )


# ═══════════════════════════════════════════════════════════════
#  Abstract Base Connector
# ═══════════════════════════════════════════════════════════════

class BaseConnector(ABC):
    """Abstract base class every ingestion connector must extend.

    Subclasses implement the four abstract methods.  `save_event` is a
    concrete helper shared by all connectors.
    """

    # ── Abstract interface ────────────────────────────────────

    @abstractmethod
    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Verify the authenticity of an incoming webhook request.

        Args:
            request_body: Raw bytes of the HTTP request body.
            headers: HTTP headers as a flat dict (lower-cased keys).

        Returns:
            ``True`` if the request is valid, ``False`` otherwise.
        """

    @abstractmethod
    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Transform a raw webhook / API payload into an `IngestionPayload`.

        Args:
            payload: Deserialised JSON body from the webhook.

        Returns:
            Normalised ``IngestionPayload``.
        """

    @abstractmethod
    async def fetch_content(self, source_id: str) -> str | None:
        """Fetch content from the external source by its identifier.

        Used in *polling mode* or when enrichment is needed after receiving
        a lightweight webhook notification.

        Args:
            source_id: Resource identifier (PR number, channel ID, etc.).

        Returns:
            Content string, or ``None`` if the resource cannot be found.
        """

    # ── Concrete helpers ──────────────────────────────────────

    async def save_event(self, payload: IngestionPayload) -> str:
        """Persist an ``IngestionPayload`` as an ``IngestionEvent`` row.

        Deduplication is performed by content hash: if an event with the
        same ``source_type``, ``source_id``, and ``content_hash`` already
        exists, the existing event's ID is returned instead.

        Args:
            payload: The normalised payload to persist.

        Returns:
            The ``id`` (primary key) of the created or existing event.
        """
        content_hash = payload.content_hash or self._compute_content_hash(
            payload.raw_content,
        )

        async with async_session_factory() as session:
            # Dedup check — same source + same content hash → skip insert.
            from sqlalchemy import select

            existing = await session.execute(
                select(IngestionEvent.id).where(
                    IngestionEvent.source_type == payload.source_type,
                    IngestionEvent.source_id == payload.source_id,
                    IngestionEvent.event_metadata["content_hash"].as_string() == content_hash,
                ),
            )
            row = existing.scalars().first()
            if row is not None:
                logger.info(
                    "Duplicate event skipped — source=%s id=%s hash=%s",
                    payload.source_type,
                    payload.source_id,
                    content_hash[:12],
                )
                return row

            event_id = uuid.uuid4().hex[:16]
            meta = {**payload.metadata, "content_hash": content_hash}
            event = IngestionEvent(
                id=event_id,
                source_type=payload.source_type,
                source_id=payload.source_id,
                raw_content=payload.raw_content,
                clean_content=payload.raw_content[:4000],
                event_metadata=meta,
                is_signal=False,
                signal_type=None,
                processed_at=None,
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)
            await session.commit()

            logger.info(
                "Ingestion event saved — id=%s source=%s/%s type=%s",
                event_id,
                payload.source_type,
                payload.source_id,
                payload.event_type,
            )
            return event_id

    # ── Utility ───────────────────────────────────────────────

    @staticmethod
    def _compute_content_hash(content: str) -> str:
        """Return the SHA-256 hex digest of *content* for deduplication.

        Args:
            content: Arbitrary text to hash.

        Returns:
            64-character lowercase hex string.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
