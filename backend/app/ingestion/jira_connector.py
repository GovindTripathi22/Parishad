"""
CortexOS Ingestion — Jira & Linear Connectors.

Handles Jira Cloud/Server webhook payloads, REST API v3 polling via
Basic auth, and provides a ``LinearConnector`` subclass for Linear
webhook format.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.ingestion.base_connector import BaseConnector, EventType, IngestionPayload

logger = logging.getLogger(__name__)


class JiraConnector(BaseConnector):
    """Connector for Jira Cloud / Server webhooks and REST API v3."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url: str = settings.JIRA_BASE_URL.rstrip("/")
        self._api_token: str = settings.JIRA_API_TOKEN
        self._user_email: str = settings.JIRA_USER_EMAIL

    # ── Webhook Validation ────────────────────────────────────

    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Validate a Jira webhook request.

        Jira Cloud does not sign webhook payloads with HMAC by default.
        Validation relies on a shared secret token passed in a custom
        header or query parameter configured in the webhook registration.

        For now, this implementation accepts all requests when no secret
        is configured, and checks ``X-Atlassian-Webhook-Secret`` if one
        is present in the headers.
        """
        # If the Jira admin has configured a webhook secret header:
        secret_header = headers.get("x-atlassian-webhook-secret", "")
        if secret_header:
            # Compare against the API token as a shared secret.
            if secret_header != self._api_token:
                logger.warning("Jira webhook secret mismatch.")
                return False

        # Basic sanity: body must be non-empty JSON.
        if not request_body or len(request_body) < 2:
            logger.warning("Empty Jira webhook body.")
            return False

        return True

    # ── Event Parsing ─────────────────────────────────────────

    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Parse a Jira webhook payload.

        Supported webhook events:
        - ``jira:issue_created``
        - ``jira:issue_updated`` (including resolution transitions)
        - ``comment_created``

        The ``webhookEvent`` field tells us the event category.
        """
        webhook_event = payload.get("webhookEvent", "")
        issue_data = payload.get("issue", {})
        comment_data = payload.get("comment")

        if comment_data and webhook_event in ("comment_created", "jira:issue_updated"):
            return self._parse_comment(issue_data, comment_data, payload)

        if webhook_event == "jira:issue_created":
            return self._parse_issue(issue_data, EventType.TICKET_CREATED, payload)

        if webhook_event == "jira:issue_updated":
            # Check if this is a resolution (status changed to Done/Resolved).
            changelog = payload.get("changelog", {})
            resolved = self._is_resolution(changelog)
            event_type = EventType.TICKET_RESOLVED if resolved else EventType.TICKET_UPDATED
            return self._parse_issue(issue_data, event_type, payload)

        # Fallthrough.
        logger.info("Unknown Jira webhook event: %s", webhook_event)
        return IngestionPayload(
            source_type="jira",
            source_id="unknown",
            raw_content=str(payload)[:4000],
            event_type=EventType.UNKNOWN,
            metadata={"webhookEvent": webhook_event},
            content_hash=self._compute_content_hash(str(payload)),
        )

    # ── Issue Parsing ─────────────────────────────────────────

    def _parse_issue(
        self,
        issue: dict[str, Any],
        event_type: EventType,
        outer: dict[str, Any],
    ) -> IngestionPayload:
        key = issue.get("key", "UNKNOWN-0")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        description = (fields.get("description") or "")[:2000]
        status = fields.get("status", {}).get("name", "unknown")
        priority = fields.get("priority", {}).get("name", "unset")
        assignee_obj = fields.get("assignee") or {}
        assignee = assignee_obj.get("displayName", "Unassigned")
        labels = fields.get("labels", [])

        user = outer.get("user", {}).get("displayName", assignee)

        if event_type == EventType.TICKET_RESOLVED:
            text = (
                f"Ticket {key} (priority: {priority}) resolved by @{user}: "
                f"{summary}. Resolution: {description}"
            )
        elif event_type == EventType.TICKET_CREATED:
            text = (
                f"Ticket {key} (priority: {priority}) created by @{user}: "
                f"{summary}. Description: {description}"
            )
        else:
            text = (
                f"Ticket {key} (priority: {priority}) updated — status: {status}. "
                f"Summary: {summary}. Assignee: {assignee}"
            )

        return IngestionPayload(
            source_type="jira",
            source_id=key,
            raw_content=text,
            event_type=event_type,
            metadata={
                "issue_key": key,
                "summary": summary,
                "status": status,
                "priority": priority,
                "assignee": assignee,
                "labels": labels,
                "description_preview": description[:500],
            },
            content_hash=self._compute_content_hash(text),
        )

    # ── Comment Parsing ───────────────────────────────────────

    def _parse_comment(
        self,
        issue: dict[str, Any],
        comment: dict[str, Any],
        outer: dict[str, Any],
    ) -> IngestionPayload:
        key = issue.get("key", "UNKNOWN-0")
        author = comment.get("author", {}).get("displayName", "unknown")
        body = (comment.get("body") or "")[:2000]
        comment_id = comment.get("id", "?")

        summary = f"Comment by @{author} on {key}: {body}"

        return IngestionPayload(
            source_type="jira",
            source_id=f"{key}-comment-{comment_id}",
            raw_content=summary,
            event_type=EventType.COMMENT,
            metadata={
                "issue_key": key,
                "comment_id": comment_id,
                "author": author,
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _is_resolution(changelog: dict[str, Any]) -> bool:
        """Check whether a changelog contains a transition to a resolved status."""
        done_statuses = {"done", "resolved", "closed", "complete", "completed"}
        for item in changelog.get("items", []):
            if item.get("field", "").lower() == "status":
                to_string = (item.get("toString") or "").lower()
                if to_string in done_statuses:
                    return True
        return False

    # ── Polling / API Fetch ───────────────────────────────────

    async def fetch_content(self, source_id: str) -> str | None:
        """Fetch issue details from the Jira REST API v3 by issue key.

        ``source_id`` should be a Jira issue key (e.g. ``"PROJ-123"``).

        Supports both Jira Cloud (``/rest/api/3/issue/{key}``) and Jira
        Server (``/rest/api/2/issue/{key}``) by falling back to v2 if v3
        returns 404.
        """
        if not self._base_url or not self._api_token:
            logger.warning("Jira credentials not configured — cannot fetch content.")
            return None

        issue_key = source_id.split("-comment-")[0]  # Strip comment suffix if any.

        async with httpx.AsyncClient(timeout=30.0) as client:
            for api_version in ("3", "2"):
                url = f"{self._base_url}/rest/api/{api_version}/issue/{issue_key}"
                resp = await client.get(url, headers=self._auth_headers())

                if resp.status_code == 200:
                    data = resp.json()
                    fields = data.get("fields", {})
                    summary = fields.get("summary", "")
                    description = (fields.get("description") or "")[:2000]
                    status = fields.get("status", {}).get("name", "unknown")
                    priority = fields.get("priority", {}).get("name", "unset")
                    assignee_obj = fields.get("assignee") or {}
                    assignee = assignee_obj.get("displayName", "Unassigned")
                    labels = fields.get("labels", [])
                    comments_data = (
                        fields.get("comment", {}).get("comments", [])
                    )
                    recent_comments = [
                        f"  - {c.get('author', {}).get('displayName', '?')}: "
                        f"{(c.get('body') or '')[:300]}"
                        for c in comments_data[-5:]
                    ]

                    return (
                        f"{issue_key} [{status}] (priority: {priority}, "
                        f"assignee: {assignee}): {summary}\n"
                        f"Labels: {labels}\n"
                        f"Description: {description}\n"
                        f"Recent comments:\n" + "\n".join(recent_comments)
                    )

                if resp.status_code == 404 and api_version == "3":
                    logger.info("Jira v3 returned 404, falling back to v2.")
                    continue

                logger.error(
                    "Jira API request failed: %s %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

        return None

    def _auth_headers(self) -> dict[str, str]:
        """Build Basic-auth headers for Jira (email:api_token, base64)."""
        credentials = f"{self._user_email}:{self._api_token}"
        b64 = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {b64}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }


# ═══════════════════════════════════════════════════════════════
#  LINEAR CONNECTOR — Subclass for Linear webhook format
# ═══════════════════════════════════════════════════════════════

class LinearConnector(JiraConnector):
    """Connector that parses Linear.app webhook payloads.

    Linear webhooks use a different shape than Jira:
    ``{ "action": "create|update|remove", "type": "Issue|Comment|...",
        "data": { ... }, "url": "..." }``

    This subclass re-maps the Linear format into the internal
    ``IngestionPayload`` model, reusing the save/dedup logic from
    ``BaseConnector``.
    """

    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Validate a Linear webhook.

        Linear signs webhooks with a shared secret in the
        ``Linear-Signature`` header (HMAC-SHA256, hex). If no signing
        secret is configured we accept all payloads (for dev).
        """
        # Minimal validation: non-empty body.
        if not request_body or len(request_body) < 2:
            logger.warning("Empty Linear webhook body.")
            return False
        return True

    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Parse a Linear webhook payload.

        Expected shape:
        ```json
        {
          "action": "create" | "update" | "remove",
          "type": "Issue" | "Comment" | "Project" | ...,
          "data": { "id": "...", "title": "...", ... },
          "url": "https://linear.app/..."
        }
        ```
        """
        action = payload.get("action", "")
        resource_type = payload.get("type", "")
        data = payload.get("data", {})
        url = payload.get("url", "")

        identifier = data.get("identifier") or data.get("id", "unknown")
        title = data.get("title", "")
        description = (data.get("description") or "")[:2000]
        state_name = data.get("state", {}).get("name", "") if isinstance(data.get("state"), dict) else ""
        assignee = data.get("assignee", {}).get("name", "Unassigned") if isinstance(data.get("assignee"), dict) else "Unassigned"
        priority_label = data.get("priorityLabel", "unset")
        labels = [l.get("name", "") for l in data.get("labels", {}).get("nodes", [])] if isinstance(data.get("labels"), dict) else []

        # Map Linear action → EventType.
        event_type = EventType.UNKNOWN
        if resource_type == "Issue":
            if action == "create":
                event_type = EventType.TICKET_CREATED
            elif action == "update":
                resolved_states = {"done", "completed", "canceled", "cancelled"}
                event_type = (
                    EventType.TICKET_RESOLVED
                    if state_name.lower() in resolved_states
                    else EventType.TICKET_UPDATED
                )
            elif action == "remove":
                event_type = EventType.TICKET_UPDATED
        elif resource_type == "Comment":
            event_type = EventType.COMMENT

        summary = (
            f"[Linear] {resource_type} {action}: {identifier} — {title}. "
            f"Status: {state_name}. Priority: {priority_label}. "
            f"Assignee: {assignee}. Description: {description}"
        )

        return IngestionPayload(
            source_type="linear",
            source_id=str(identifier),
            raw_content=summary,
            event_type=event_type,
            metadata={
                "linear_action": action,
                "resource_type": resource_type,
                "identifier": identifier,
                "title": title,
                "status": state_name,
                "priority": priority_label,
                "assignee": assignee,
                "labels": labels,
                "url": url,
            },
            content_hash=self._compute_content_hash(summary),
        )

    async def fetch_content(self, source_id: str) -> str | None:
        """Linear does not expose a simple REST fetch — return None.

        For full Linear integration, use the Linear GraphQL API.
        """
        logger.info(
            "LinearConnector.fetch_content is a no-op; "
            "use the Linear GraphQL API for polling."
        )
        return None
