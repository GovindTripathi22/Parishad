"""
Parishad Ingestion — Google Drive Connector.

Handles Google Drive push notifications (change events), document
content export via the Drive API, and OAuth2 service-account token
management.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.config import get_settings
from app.ingestion.base_connector import BaseConnector, EventType, IngestionPayload

logger = logging.getLogger(__name__)

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_DRIVE_EXPORT_API = "https://www.googleapis.com/drive/v3/files"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# MIME types we know how to export.
_EXPORT_MIME_MAP: dict[str, tuple[str, str]] = {
    # Google Docs → plain text
    "application/vnd.google-apps.document": ("text/plain", "txt"),
    # Google Sheets → CSV
    "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
    # Google Slides → plain text
    "application/vnd.google-apps.presentation": ("text/plain", "txt"),
}


class GDriveConnector(BaseConnector):
    """Connector for Google Drive push notifications and the Drive API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._credentials_json: str = settings.GDRIVE_CREDENTIALS_JSON
        self._credentials: dict[str, Any] | None = None
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

        # Parse credentials eagerly so we fail fast on misconfiguration.
        if self._credentials_json:
            try:
                self._credentials = json.loads(self._credentials_json)
            except json.JSONDecodeError:
                logger.error(
                    "GDRIVE_CREDENTIALS_JSON is not valid JSON — "
                    "Google Drive connector will be disabled."
                )
                self._credentials = None
        else:
            logger.info(
                "GDRIVE_CREDENTIALS_JSON is empty — "
                "Google Drive connector is disabled."
            )

    @property
    def is_configured(self) -> bool:
        """Whether the connector has valid credentials."""
        return self._credentials is not None

    # ── Webhook Validation ────────────────────────────────────

    async def validate_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Validate a Google Drive push notification.

        Google Drive notifications include:
        - ``X-Goog-Channel-ID``
        - ``X-Goog-Resource-State`` (sync, update, trash, add, …)
        - ``X-Goog-Resource-ID``

        The initial ``sync`` message is always accepted (it confirms the
        channel was set up).  All others are accepted if they carry
        valid Google headers.
        """
        resource_state = headers.get("x-goog-resource-state", "")
        channel_id = headers.get("x-goog-channel-id", "")
        resource_id = headers.get("x-goog-resource-id", "")

        if not resource_state:
            logger.warning("Missing X-Goog-Resource-State — rejecting.")
            return False

        if resource_state == "sync":
            logger.info("Google Drive sync notification for channel=%s.", channel_id)
            return True

        if not resource_id:
            logger.warning("Missing X-Goog-Resource-ID — rejecting.")
            return False

        return True

    # ── Event Parsing ─────────────────────────────────────────

    async def parse_event(self, payload: dict[str, Any]) -> IngestionPayload:
        """Parse a Google Drive push notification.

        The push notification body is typically empty — metadata is
        conveyed entirely through HTTP headers which the caller passes
        as the ``payload`` dict under the ``"_headers"`` key, or as a
        combined dict.
        """
        headers = payload.get("_headers", payload)
        resource_state = headers.get("x-goog-resource-state", "unknown")
        resource_id = headers.get("x-goog-resource-id", "unknown")
        channel_id = headers.get("x-goog-channel-id", "")
        changed = headers.get("x-goog-changed", "")

        event_map: dict[str, EventType] = {
            "update": EventType.DOC_UPDATED,
            "add": EventType.DOC_ADDED,
            "trash": EventType.DOC_TRASHED,
            "untrash": EventType.DOC_UPDATED,
            "change": EventType.DOC_UPDATED,
        }
        event_type = event_map.get(resource_state, EventType.UNKNOWN)

        # Attempt to enrich with file metadata.
        file_name = "unknown"
        mime_type = "unknown"
        content_snippet = ""
        if self.is_configured and resource_id != "unknown":
            try:
                meta = await self._get_file_metadata(resource_id)
                if meta:
                    file_name = meta.get("name", file_name)
                    mime_type = meta.get("mimeType", mime_type)
            except Exception:
                logger.warning(
                    "Failed to enrich metadata for resource_id=%s.",
                    resource_id,
                    exc_info=True,
                )

        summary = (
            f"Google Drive {resource_state}: file '{file_name}' "
            f"(id={resource_id}, type={mime_type}). Changed: {changed}"
        )

        return IngestionPayload(
            source_type="gdrive",
            source_id=resource_id,
            raw_content=summary,
            event_type=event_type,
            metadata={
                "resource_state": resource_state,
                "resource_id": resource_id,
                "channel_id": channel_id,
                "changed_fields": changed,
                "file_name": file_name,
                "mime_type": mime_type,
            },
            content_hash=self._compute_content_hash(summary),
        )

    # ── Polling / API Fetch ───────────────────────────────────

    async def fetch_content(self, source_id: str) -> str | None:
        """Download a file's content from Google Drive by file ID.

        - Google Docs → exported as plain text
        - Google Sheets → exported as CSV
        - Plain text / other → direct download (``alt=media``)

        Returns the content as a string, or ``None`` on failure.
        """
        if not self.is_configured:
            logger.info("GDrive connector not configured — skipping fetch.")
            return None

        token = await self._ensure_access_token()
        if not token:
            return None

        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"Authorization": f"Bearer {token}"}

            # Get file metadata first to determine MIME type.
            meta = await self._get_file_metadata(source_id)
            if not meta:
                return None

            mime_type = meta.get("mimeType", "")
            file_name = meta.get("name", source_id)

            # Google Workspace document → use export endpoint.
            if mime_type in _EXPORT_MIME_MAP:
                export_mime, _ = _EXPORT_MIME_MAP[mime_type]
                url = f"{_DRIVE_EXPORT_API}/{source_id}/export"
                resp = await client.get(
                    url,
                    headers=headers,
                    params={"mimeType": export_mime},
                )
            else:
                # Binary / plain text → direct download.
                url = f"{_DRIVE_EXPORT_API}/{source_id}"
                resp = await client.get(
                    url,
                    headers=headers,
                    params={"alt": "media"},
                )

            if resp.status_code != 200:
                logger.error(
                    "GDrive fetch failed for %s (%s): %s %s",
                    file_name,
                    source_id,
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            content = resp.text
            logger.info(
                "Fetched %d chars from GDrive file '%s' (%s).",
                len(content),
                file_name,
                source_id,
            )
            return content

    # ── File Metadata ─────────────────────────────────────────

    async def _get_file_metadata(self, file_id: str) -> dict[str, Any] | None:
        """Fetch metadata (name, mimeType) for a Drive file."""
        token = await self._ensure_access_token()
        if not token:
            return None

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "id,name,mimeType,modifiedTime,trashed"},
            )
            if resp.status_code != 200:
                logger.warning(
                    "GDrive metadata request failed for %s: %s",
                    file_id,
                    resp.status_code,
                )
                return None
            return resp.json()

    # ── OAuth2 Token Management ───────────────────────────────

    async def _ensure_access_token(self) -> str | None:
        """Return a valid access token, refreshing if necessary.

        Uses the service-account private key from ``GDRIVE_CREDENTIALS_JSON``
        to mint a signed JWT and exchange it for an access token at
        Google's OAuth2 token endpoint.
        """
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        if not self._credentials:
            logger.warning("No GDrive credentials — cannot obtain token.")
            return None

        try:
            token = await self._refresh_service_account_token()
            return token
        except Exception:
            logger.error("Failed to refresh GDrive access token.", exc_info=True)
            return None

    async def _refresh_service_account_token(self) -> str | None:
        """Exchange a service-account JWT for an access token.

        This uses the ``google.auth`` library if available, otherwise
        falls back to manual JWT construction with ``PyJWT``.
        """
        creds = self._credentials
        if not creds:
            return None

        # Try google-auth library first (preferred).
        try:
            return await self._refresh_via_google_auth(creds)
        except ImportError:
            pass

        # Fallback: manual JWT with PyJWT + cryptography.
        try:
            return await self._refresh_via_pyjwt(creds)
        except ImportError:
            logger.error(
                "Neither google-auth nor PyJWT is installed — "
                "cannot refresh GDrive token."
            )
            return None

    async def _refresh_via_google_auth(self, creds: dict[str, Any]) -> str:
        """Use ``google.oauth2.service_account.Credentials`` to get a token."""
        from google.auth.transport.requests import Request  # type: ignore[import-untyped]
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        credentials = service_account.Credentials.from_service_account_info(
            creds,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        credentials.refresh(Request())
        self._access_token = credentials.token
        self._token_expiry = (
            credentials.expiry.timestamp() if credentials.expiry else time.time() + 3600
        )
        logger.info("GDrive token refreshed via google-auth (expires in ~1h).")
        return self._access_token  # type: ignore[return-value]

    async def _refresh_via_pyjwt(self, creds: dict[str, Any]) -> str | None:
        """Manually construct a signed JWT and exchange it for a token."""
        import jwt  # PyJWT

        now = int(time.time())
        claim_set = {
            "iss": creds.get("client_email", ""),
            "scope": "https://www.googleapis.com/auth/drive.readonly",
            "aud": _TOKEN_ENDPOINT,
            "iat": now,
            "exp": now + 3600,
        }
        private_key = creds.get("private_key", "")
        assertion = jwt.encode(claim_set, private_key, algorithm="RS256")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _TOKEN_ENDPOINT,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            if resp.status_code != 200:
                logger.error(
                    "GDrive token exchange failed: %s %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            token_data = resp.json()
            self._access_token = token_data["access_token"]
            self._token_expiry = time.time() + token_data.get("expires_in", 3600)
            logger.info("GDrive token refreshed via PyJWT (expires in ~1h).")
            return self._access_token
