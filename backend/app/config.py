"""
CortexOS Configuration — Pydantic Settings for all services.
"""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CANONICAL_DB_FILE = (PROJECT_ROOT / "cortexos.db").as_posix()


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env file."""

    # ── App ─────────────────────────────────────────────────────
    APP_NAME: str = "CortexOS"
    APP_VERSION: str = "3.0.0"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "*"

    # ── Database ────────────────────────────────────────────────
    DATABASE_URL: str = f"sqlite+aiosqlite:///{CANONICAL_DB_FILE}"
    DATABASE_ECHO: bool = False

    # ── Gemini AI ───────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"

    # ── Ingestion Connectors ────────────────────────────────────
    GITHUB_WEBHOOK_SECRET: str = ""
    GITHUB_TOKEN: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    JIRA_BASE_URL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_USER_EMAIL: str = ""
    GDRIVE_CREDENTIALS_JSON: str = ""

    # ── Security ────────────────────────────────────────────────
    SECRET_KEY: str = "cortexos-dev-secret-change-in-production"
    ADMIN_API_KEY: str = "cortexos-admin-key"

    # ── Vector Store ────────────────────────────────────────────
    VECTOR_DIMENSION: int = 768
    SIMILARITY_THRESHOLD: float = 0.35

    # ── Council of Agents ───────────────────────────────────────
    COUNCIL_ENABLED: bool = True
    COUNCIL_CONFIDENCE_THRESHOLD: float = 0.7

    # ── Parishad Review Specialist ──────────────────────────────
    PARISHAD_LOCAL_SUMMARY_ENABLED: bool = False
    PARISHAD_LOCAL_MODEL_PATH: str = "training/saved_model"
    PARISHAD_LOCAL_INFERENCE_DEVICE: str = "auto"
    PARISHAD_LOCAL_TIMEOUT_SECONDS: float = 15.0
    PARISHAD_LOCAL_MAX_NEW_TOKENS: int = 1536

    # ── Paths ───────────────────────────────────────────────────
    BASE_DIR: str = str(Path(__file__).resolve().parent.parent)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
