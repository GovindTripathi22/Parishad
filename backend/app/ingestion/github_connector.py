"""
CortexOS Ingestion — GitHub Connector.

Handles GitHub webhook signature verification, event parsing for pull
requests, pushes, issues, and issue comments, plus REST API polling.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.ingestion.base_connector import BaseConnector, EventType, IngestionPayload

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


class GitHubConnector(BaseConnector):
    """Connector for GitHub webhooks and the GitHub REST API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._webhook_secret: str = settings.GITHUB_WEBHOOK_SECRET
        self._token: str = settings.GITHUB_TOKEN

    # ── Webhook Validation ────────────────────────────────────

    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Validate the GitHub webhook HMAC-SHA256 signature.

        GitHub sends the signature in the ``X-Hub-Signature-256`` header
        as ``sha256=<hex_digest>``.
        """
        signature_header = headers.get("x-hub-signature-256", "")
        if not signature_header.startswith("sha256="):
            logger.warning("Missing or malformed X-Hub-Signature-256 header.")
            return False

        expected = hmac.new(
            self._webhook_secret.encode("utf-8"),
            request_body,
            hashlib.sha256,
        ).hexdigest()

        received = signature_header.removeprefix("sha256=")
        valid = hmac.compare_digest(expected, received)
        if not valid:
            logger.warning("GitHub webhook signature mismatch.")
        return valid

    # ── Event Parsing ─────────────────────────────────────────

    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Parse a GitHub webhook payload into an ``IngestionPayload``.

        Supported event types
        (determined by the ``action`` field + payload shape):
        - ``pull_request`` — merged / opened / closed
        - ``push``
        - ``issues`` — opened / closed
        - ``issue_comment`` — created
        """
        # Determine which GitHub event category we're dealing with.
        if "pull_request" in payload:
            return self._parse_pull_request(payload)
        if "commits" in payload and "ref" in payload:
            return self._parse_push(payload)
        if "issue" in payload and "comment" in payload:
            return self._parse_issue_comment(payload)
        if "issue" in payload:
            return self._parse_issue(payload)

        # Fall-through for unknown shapes.
        logger.info("Unknown GitHub event shape — storing raw payload.")
        return IngestionPayload(
            source_type="github",
            source_id="unknown",
            raw_content=str(payload)[:4000],
            event_type=EventType.UNKNOWN,
            metadata={"raw_keys": list(payload.keys())},
            content_hash=self._compute_content_hash(str(payload)),
        )

    # ── PR ────────────────────────────────────────────────────

    def _parse_pull_request(self, payload: dict[str, Any]) -> IngestionPayload:
        action = payload.get("action", "")
        pr = payload["pull_request"]
        number = pr.get("number", "?")
        title = pr.get("title", "")
        body = (pr.get("body") or "")[:2000]
        author = pr.get("user", {}).get("login", "unknown")
        merged = pr.get("merged", False)
        merge_commit = pr.get("merge_commit_sha", "")[:8]
        changed_files = pr.get("changed_files", 0)

        # Collect changed file names from the payload if available.
        files_list: list[str] = []
        if "files" in payload:
            files_list = [f.get("filename", "") for f in payload.get("files", [])]

        if action == "closed" and merged:
            event_type = EventType.PR_MERGED
            summary = (
                f"PR #{number} merged by @{author}: {title}. "
                f"Changed files ({changed_files}): {files_list or 'see API'}. "
                f"Merge commit: {merge_commit}. "
                f"Description: {body}"
            )
        elif action == "opened":
            event_type = EventType.PR_OPENED
            summary = (
                f"PR #{number} opened by @{author}: {title}. "
                f"Description: {body}"
            )
        elif action == "closed":
            event_type = EventType.PR_CLOSED
            summary = f"PR #{number} closed (not merged) by @{author}: {title}."
        else:
            event_type = EventType.UNKNOWN
            summary = f"PR #{number} action={action} by @{author}: {title}."

        return IngestionPayload(
            source_type="github",
            source_id=f"pr-{number}",
            raw_content=summary,
            event_type=event_type,
            metadata={
                "pr_number": number,
                "author": author,
                "action": action,
                "merged": merged,
                "changed_files": changed_files,
                "files": files_list,
                "merge_commit": merge_commit,
                "title": title,
                "body_preview": body[:500],
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Push ──────────────────────────────────────────────────

    def _parse_push(self, payload: dict[str, Any]) -> IngestionPayload:
        ref = payload.get("ref", "")
        pusher = payload.get("pusher", {}).get("name", "unknown")
        commits = payload.get("commits", [])
        commit_msgs = [c.get("message", "")[:200] for c in commits[:10]]
        head_sha = payload.get("after", "")[:8]

        summary = (
            f"Push to {ref} by @{pusher} ({len(commits)} commits). "
            f"Head: {head_sha}. Messages: {commit_msgs}"
        )

        return IngestionPayload(
            source_type="github",
            source_id=f"push-{head_sha}",
            raw_content=summary,
            event_type=EventType.PUSH,
            metadata={
                "ref": ref,
                "pusher": pusher,
                "commit_count": len(commits),
                "head_sha": head_sha,
                "commit_messages": commit_msgs,
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Issues ────────────────────────────────────────────────

    def _parse_issue(self, payload: dict[str, Any]) -> IngestionPayload:
        action = payload.get("action", "")
        issue = payload["issue"]
        number = issue.get("number", "?")
        title = issue.get("title", "")
        body = (issue.get("body") or "")[:2000]
        author = issue.get("user", {}).get("login", "unknown")
        labels = [l.get("name", "") for l in issue.get("labels", [])]

        event_type = (
            EventType.ISSUE_OPENED if action == "opened"
            else EventType.ISSUE_CLOSED if action == "closed"
            else EventType.UNKNOWN
        )

        summary = (
            f"Issue #{number} {action} by @{author}: {title}. "
            f"Labels: {labels}. Body: {body}"
        )

        return IngestionPayload(
            source_type="github",
            source_id=f"issue-{number}",
            raw_content=summary,
            event_type=event_type,
            metadata={
                "issue_number": number,
                "author": author,
                "action": action,
                "title": title,
                "labels": labels,
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Issue Comment ─────────────────────────────────────────

    def _parse_issue_comment(self, payload: dict[str, Any]) -> IngestionPayload:
        comment = payload["comment"]
        issue = payload["issue"]
        author = comment.get("user", {}).get("login", "unknown")
        body = (comment.get("body") or "")[:2000]
        issue_number = issue.get("number", "?")
        comment_id = comment.get("id", "?")

        summary = (
            f"Comment by @{author} on issue #{issue_number}: {body}"
        )

        return IngestionPayload(
            source_type="github",
            source_id=f"comment-{comment_id}",
            raw_content=summary,
            event_type=EventType.COMMENT,
            metadata={
                "issue_number": issue_number,
                "comment_id": comment_id,
                "author": author,
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Polling / API Fetch ───────────────────────────────────

    async def fetch_content(self, source_id: str) -> str | None:
        """Fetch PR details from the GitHub REST API by PR number.

        ``source_id`` should be a raw PR number (e.g. ``"123"``).
        The method extracts the repo from the PR URL if available,
        otherwise expects ``OWNER/REPO`` to be derivable from the
        token's default context.

        Returns a summary string or ``None`` on failure.
        """
        if not self._token:
            logger.warning("GITHUB_TOKEN not configured — cannot fetch content.")
            return None

        pr_number = source_id.replace("pr-", "")

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Search for the PR across all repos accessible to the token.
            search_url = f"{_GITHUB_API}/search/issues"
            params = {"q": f"is:pr {pr_number}", "per_page": 1}
            headers = self._auth_headers()

            resp = await client.get(search_url, headers=headers, params=params)
            self._check_rate_limit(resp)

            if resp.status_code != 200:
                logger.error("GitHub search failed: %s %s", resp.status_code, resp.text[:300])
                return None

            items = resp.json().get("items", [])
            if not items:
                logger.info("No PR found for source_id=%s", source_id)
                return None

            pr_url = items[0].get("pull_request", {}).get("url")
            if not pr_url:
                return items[0].get("title")

            pr_resp = await client.get(pr_url, headers=headers)
            self._check_rate_limit(pr_resp)
            if pr_resp.status_code != 200:
                return None

            pr = pr_resp.json()
            title = pr.get("title", "")
            body = (pr.get("body") or "")[:2000]
            author = pr.get("user", {}).get("login", "unknown")
            merged = pr.get("merged", False)
            state = "merged" if merged else pr.get("state", "unknown")
            changed = pr.get("changed_files", 0)

            return (
                f"PR #{pr_number} ({state}) by @{author}: {title}. "
                f"Changed files: {changed}. Description: {body}"
            )

    # ── Internal Helpers ──────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _check_rate_limit(response: httpx.Response) -> None:
        """Log a warning when the GitHub API rate limit is nearly exhausted."""
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            remaining_int = int(remaining)
            if remaining_int <= 10:
                reset = response.headers.get("x-ratelimit-reset", "?")
                logger.warning(
                    "GitHub API rate limit nearly exhausted: %d remaining. "
                    "Resets at epoch %s.",
                    remaining_int,
                    reset,
                )
