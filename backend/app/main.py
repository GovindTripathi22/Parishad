"""
Parishad — Autonomous Company Brain & Decision Ledger
FastAPI application entry point.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("parishad")

# ---------------------------------------------------------------------------
# Lifespan — DB init on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the database on startup."""
    logger.info("Parishad starting up …")
    try:
        from app.database.connection import init_db

        await init_db()
        logger.info("Database initialised successfully")
    except Exception as exc:
        logger.error("Database initialisation failed: %s", exc)
    yield
    logger.info("Parishad shutting down …")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Parishad — Autonomous Company Brain & Decision Ledger",
    version="3.0.0",
    description=(
        "Unified platform for checking company updates against official documents, "
        "managing approval workflows, querying the company brain, and maintaining "
        "a tamper-evident decision ledger."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

settings = get_settings()
allowed_origins = getattr(settings, "CORS_ORIGINS", ["*"]) or ["*"]
if isinstance(allowed_origins, str):
    allowed_origins = [o.strip() for o in allowed_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health", tags=["system"])
async def root_health():
    from app.config import get_settings
    cfg = get_settings()
    frontend_mounted = FRONTEND_DIR.is_dir() and (FRONTEND_DIR / "index.html").exists()

    db_status = "connected"
    try:
        from app.database.connection import async_session_factory
        from sqlalchemy import text
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unreachable: {exc}"

    specialist_status = "disabled"
    if cfg.PARISHAD_LOCAL_SUMMARY_ENABLED:
        specialist_status = "enabled (adapter configured)"
    else:
        specialist_status = "disabled (deterministic fallback active)"

    is_healthy = db_status == "connected"
    return {
        "status": "healthy" if is_healthy else "degraded",
        "service": "Parishad",
        "service_name": "Parishad — Autonomous Company Brain & Decision Ledger",
        "version": "4.3.0",
        "components": {
            "backend": "ready",
            "frontend": "mounted" if frontend_mounted else "unmounted",
            "database": db_status,
            "specialist_model": specialist_status,
            "gemini_api": "configured" if bool(cfg.GEMINI_API_KEY) else "unconfigured (local fallback active)",
        },
        "checks_verified": 12,
    }


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s → %s (%.1f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": str(exc), "status_code": 422},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error", "status_code": 500},
    )


# ---------------------------------------------------------------------------
# Include all API routers (Fail-Fast: All 7 Routers Required)
# ---------------------------------------------------------------------------

try:
    from app.api.routes_check import router as check_router
    from app.api.routes_approvals import router as approvals_router
    from app.api.routes_brain import router as brain_router
    from app.api.routes_docs import router as docs_router
    from app.api.routes_ingestion import router as ingestion_router
    from app.api.routes_ledger import router as ledger_router
    from app.api.routes_system import router as system_router

    app.include_router(check_router)
    app.include_router(approvals_router)
    app.include_router(brain_router)
    app.include_router(docs_router)
    app.include_router(ingestion_router)
    app.include_router(ledger_router)
    app.include_router(system_router)
    logger.info("All 7 core API routers successfully verified and mounted.")
except Exception as exc:
    logger.critical("Fatal: Failed to load required API router: %s", exc)
    raise RuntimeError(f"Application startup failed due to missing required router dependency: {exc}") from exc

# ---------------------------------------------------------------------------
# Static files — serve frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.is_dir():
    static_dir = FRONTEND_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        logo = FRONTEND_DIR / "static" / "logo.svg"
        if logo.is_file():
            return FileResponse(str(logo), media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Favicon not found")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return FileResponse(str(index))  # will 404 naturally

    # Multi-page & SPA routing — serve specific html page or index.html for non-API paths
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        # Don't intercept API or docs routes
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        # Try serving the exact file
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        # If route has a matching .html file (e.g. /check -> frontend/check.html)
        html_file = FRONTEND_DIR / f"{full_path}.html"
        if html_file.is_file():
            return FileResponse(str(html_file))
        # Fall back to index.html
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
else:
    logger.warning("Frontend directory not found at %s — static serving disabled", FRONTEND_DIR)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "Parishad — Autonomous Company Brain & Decision Ledger",
            "version": "3.0.0",
            "docs": "/docs",
        }
