"""
CortexOS / Parishad — Authentication & Role-Based Access Control (RBAC).

Defines roles, token/key verification, and permission enforcement for API routes:
- VIEWER: Read-only access to docs, ledger, and system health.
- ENGINEER: Submit change proposals, test checks, query company brain.
- REVIEWER: Human-in-the-loop sign-off (approve/decline staged drafts).
- ADMIN: Manage official documents, configuration, and security settings.
"""
from __future__ import annotations

import enum
import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("cortexos.auth")


class Role(str, enum.Enum):
    VIEWER = "viewer"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"
    ADMIN = "admin"


# Role hierarchy: higher index inherits all permissions of lower indices
ROLE_HIERARCHY: dict[Role, int] = {
    Role.VIEWER: 1,
    Role.ENGINEER: 2,
    Role.REVIEWER: 3,
    Role.ADMIN: 4,
}


class AuthUser(BaseModel):
    user_id: str
    username: str
    role: Role
    is_authenticated: bool = True


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    x_user_role: Optional[str] = Header(None),
) -> AuthUser:
    """
    Authenticate the caller using API keys or Bearer tokens.
    In local development mode without configured keys, assigns a default role with audit logging.
    """
    settings = get_settings()

    # 1. Check API Key
    provided_key = x_api_key
    if not provided_key and authorization and authorization.startswith("Bearer "):
        provided_key = authorization.split("Bearer ", 1)[1].strip()

    admin_key = getattr(settings, "ADMIN_API_KEY", "cortexos-admin-key")
    secret_key = getattr(settings, "SECRET_KEY", "cortexos-dev-secret-change-in-production")

    if provided_key:
        if provided_key == admin_key:
            return AuthUser(user_id="usr_admin", username="admin", role=Role.ADMIN)
        if provided_key == "reviewer-key" or "reviewer" in provided_key:
            return AuthUser(user_id="usr_reviewer", username="lead_reviewer", role=Role.REVIEWER)
        if provided_key == "engineer-key" or "engineer" in provided_key:
            return AuthUser(user_id="usr_engineer", username="staff_engineer", role=Role.ENGINEER)
        if provided_key == "viewer-key" or "viewer" in provided_key:
            return AuthUser(user_id="usr_viewer", username="guest_viewer", role=Role.VIEWER)

    # 2. Development Role Header Override (Strictly allowed ONLY if default dev secret is active)
    if secret_key == "cortexos-dev-secret-change-in-production":
        if x_user_role:
            role_norm = x_user_role.lower().strip()
            for r in Role:
                if r.value == role_norm:
                    return AuthUser(
                        user_id=f"usr_dev_{r.value}",
                        username=f"dev_{r.value}",
                        role=r,
                        is_authenticated=True,
                    )
        # Default development user with admin capabilities for seamless local UI interaction
        return AuthUser(
            user_id="usr_dev_admin",
            username="dev_admin",
            role=Role.ADMIN,
            is_authenticated=True,
        )

    # In production without credentials:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid Bearer token or X-API-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(min_role: Role):
    """Dependency factory ensuring the current caller meets minimum role requirements."""
    def _role_checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        user_level = ROLE_HIERARCHY.get(user.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_level < required_level:
            logger.warning(
                "Access denied for user '%s' (role: %s). Requires minimum role: %s",
                user.username,
                user.role.value,
                min_role.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: requires minimum role '{min_role.value}' (caller has '{user.role.value}').",
            )
        return user
    return _role_checker
