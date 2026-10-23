"""
Parishad Ingestion — Slack Connector.

Handles Slack request signature verification, Events API parsing
(messages, app_mention, url_verification), and Slack Web API polling.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.config import get_settings
from app.ingestion.base_connector import BaseConnector, EventType, IngestionPayload

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"


class SlackConnector(BaseConnector):
    """Connector for Slack Events API webhooks and the Slack Web API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._signing_secret: str = settings.SLACK_SIGNING_SECRET
        self._bot_token: str = settings.SLACK_BOT_TOKEN

    # ── Webhook Validation ────────────────────────────────────

    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Validate a Slack request using the v0 HMAC-SHA256 signing scheme.

        Slack sends:
        - ``X-Slack-Request-Timestamp`` — unix epoch string
        - ``X-Slack-Signature`` — ``v0=<hex_digest>``

        The base string is ``v0:<timestamp>:<body>``.
        """
        timestamp = headers.get("x-slack-request-timestamp", "")
        signature = headers.get("x-slack-signature", "")

        if not timestamp or not signature:
            logger.warning("Missing Slack signature headers.")
            return False

        # Reject requests older than 5 minutes to prevent replay attacks.
        try:
            ts_int = int(timestamp)
        except ValueError:
            logger.warning("Non-integer Slack timestamp: %s", timestamp)
            return False

        if abs(time.time() - ts_int) > 300:
            logger.warning("Slack request timestamp too old — possible replay.")
            return False

        base_string = f"v0:{timestamp}:{request_body.decode('utf-8')}"
        expected = (
            "v0="
            + hmac.new(
                self._signing_secret.encode("utf-8"),
                base_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )

        valid = hmac.compare_digest(expected, signature)
        if not valid:
            logger.warning("Slack request signature mismatch.")
        return valid

    # ── Event Parsing ─────────────────────────────────────────

    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Parse a Slack Events API payload.

        Handles:
        - ``url_verification`` — echo the challenge (caller reads metadata).
        - ``event_callback`` — dispatches to message / app_mention parsers.
        """
        event_type_top = payload.get("type", "")

        # ── URL Verification Challenge ──
        if event_type_top == "url_verification":
            challenge = payload.get("challenge", "")
            logger.info("Slack url_verification challenge received.")
            return IngestionPayload(
                source_type="slack",
                source_id="url_verification",
                raw_content=challenge,
                event_type="url_verification",
                metadata={"challenge": challenge, "is_challenge": True},
                content_hash=self._compute_content_hash(challenge),
            )

        # ── Event Callback ──
        event = payload.get("event", {})
        sub_type = event.get("type", "")

        if sub_type == "app_mention":
            return self._parse_app_mention(event, payload)

        if sub_type == "message":
            return self._parse_message(event, payload)

        # Unknown event type — store raw.
        logger.info("Unknown Slack event sub-type: %s", sub_type)
        return IngestionPayload(
            source_type="slack",
            source_id=event.get("channel", "unknown"),
            raw_content=str(payload)[:4000],
            event_type=EventType.UNKNOWN,
            metadata={"slack_event_type": sub_type},
            content_hash=self._compute_content_hash(str(payload)),
        )

    # ── Message ───────────────────────────────────────────────

    def _parse_message(
        self,
        event: dict[str, Any],
        outer: dict[str, Any],
    ) -> IngestionPayload:
        text = event.get("text", "")
        user = event.get("user", "")
        channel = event.get("channel", "unknown")
        thread_ts = event.get("thread_ts")
        ts = event.get("ts", "")
        subtype = event.get("subtype", "")

        # Filter out bot messages and empty messages.
        if event.get("bot_id") or subtype == "bot_message":
            logger.debug("Skipping bot message in channel=%s.", channel)
            return IngestionPayload(
                source_type="slack",
                source_id=channel,
                raw_content="[bot message — skipped]",
                event_type=EventType.UNKNOWN,
                metadata={"skipped": True, "reason": "bot_message"},
                content_hash=self._compute_content_hash(f"bot-{ts}"),
            )

        if not text.strip():
            logger.debug("Skipping empty Slack message in channel=%s.", channel)
            return IngestionPayload(
                source_type="slack",
                source_id=channel,
                raw_content="[empty message — skipped]",
                event_type=EventType.UNKNOWN,
                metadata={"skipped": True, "reason": "empty"},
                content_hash=self._compute_content_hash(f"empty-{ts}"),
            )

        summary = f"Message from <@{user}> in #{channel}: {text}"
        if thread_ts:
            summary += f" (thread: {thread_ts})"

        return IngestionPayload(
            source_type="slack",
            source_id=channel,
            raw_content=summary,
            event_type=EventType.MESSAGE,
            metadata={
                "user": user,
                "channel": channel,
                "ts": ts,
                "thread_ts": thread_ts,
                "team_id": outer.get("team_id"),
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── App Mention ───────────────────────────────────────────

    def _parse_app_mention(
        self,
        event: dict[str, Any],
        outer: dict[str, Any],
    ) -> IngestionPayload:
        text = event.get("text", "")
        user = event.get("user", "")
        channel = event.get("channel", "unknown")
        ts = event.get("ts", "")

        summary = f"App mention by <@{user}> in #{channel}: {text}"

        return IngestionPayload(
            source_type="slack",
            source_id=channel,
            raw_content=summary,
            event_type=EventType.APP_MENTION,
            metadata={
                "user": user,
                "channel": channel,
                "ts": ts,
                "team_id": outer.get("team_id"),
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Polling / API Fetch ───────────────────────────────────

    async def fetch_content(self, source_id: str) -> str | None:
        """Fetch recent messages from a Slack channel.

        ``source_id`` should be a channel ID (e.g. ``C01234ABCDE``).

        Uses the ``conversations.history`` Web API endpoint.  If the
        channel contains threaded messages, thread replies are fetched
        with ``conversations.replies`` and concatenated for context.

        Returns a combined string of the last 20 messages, or ``None``
        on failure.
        """
        if not self._bot_token:
            logger.warning("SLACK_BOT_TOKEN not configured — cannot fetch content.")
            return None

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = self._auth_headers()

            # Fetch channel history.
            resp = await client.get(
                f"{_SLACK_API}/conversations.history",
                headers=headers,
                params={"channel": source_id, "limit": 20},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error(
                    "Slack conversations.history failed: %s",
                    data.get("error", "unknown"),
                )
                return None

            messages = data.get("messages", [])
            parts: list[str] = []

            for msg in messages:
                user = msg.get("user", "bot")
                text = msg.get("text", "")
                ts = msg.get("ts", "")
                parts.append(f"[{ts}] <@{user}>: {text}")

                # If the message is a thread parent, fetch replies.
                thread_ts = msg.get("thread_ts")
                reply_count = msg.get("reply_count", 0)
                if thread_ts and reply_count > 0 and thread_ts == ts:
                    thread_parts = await self._fetch_thread(
                        client, headers, source_id, thread_ts,
                    )
                    parts.extend(thread_parts)

            return "\n".join(parts) if parts else None

    async def _fetch_thread(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        channel: str,
        thread_ts: str,
    ) -> list[str]:
        """Fetch replies for a Slack thread and return formatted lines."""
        resp = await client.get(
            f"{_SLACK_API}/conversations.replies",
            headers=headers,
            params={"channel": channel, "ts": thread_ts, "limit": 50},
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning(
                "Failed to fetch thread replies: %s",
                data.get("error", "unknown"),
            )
            return []

        parts: list[str] = []
        for reply in data.get("messages", [])[1:]:  # Skip the parent.
            user = reply.get("user", "bot")
            text = reply.get("text", "")
            ts = reply.get("ts", "")
            parts.append(f"  ↳ [{ts}] <@{user}>: {text}")
        return parts

    # ── Internal Helpers ──────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
