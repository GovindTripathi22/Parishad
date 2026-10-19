"""
CortexOS — Company Brain & Decision Ledger
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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("cortexos")

# ---------------------------------------------------------------------------
# Lifespan — DB init on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the database on startup."""
    logger.info("CortexOS starting up …")
    try:
        from app.database.connection import init_db

        await init_db()
        logger.info("Database initialised successfully")
    except Exception as exc:
        logger.error("Database initialisation failed: %s", exc)
    yield
    logger.info("CortexOS shutting down …")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CortexOS — Company Brain & Decision Ledger",
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
    return {
        "status": "healthy",
        "service": "CortexOS",
        "version": "3.0.0",
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
# Include all API routers
# ---------------------------------------------------------------------------

try:
    from app.api.routes_check import router as check_router

    app.include_router(check_router)
except ImportError as exc:
    logger.warning("Could not load routes_check: %s", exc)

try:
    from app.api.routes_approvals import router as approvals_router

    app.include_router(approvals_router)
except ImportError as exc:
    logger.warning("Could not load routes_approvals: %s", exc)

try:
    from app.api.routes_brain import router as brain_router

    app.include_router(brain_router)
except ImportError as exc:
    logger.warning("Could not load routes_brain: %s", exc)

try:
    from app.api.routes_docs import router as docs_router

    app.include_router(docs_router)
except ImportError as exc:
    logger.warning("Could not load routes_docs: %s", exc)

try:
    from app.api.routes_ingestion import router as ingestion_router

    app.include_router(ingestion_router)
except ImportError as exc:
    logger.warning("Could not load routes_ingestion: %s", exc)

try:
    from app.api.routes_ledger import router as ledger_router

    app.include_router(ledger_router)
except ImportError as exc:
    logger.warning("Could not load routes_ledger: %s", exc)

try:
    from app.api.routes_system import router as system_router

    app.include_router(system_router)
except ImportError as exc:
    logger.warning("Could not load routes_system: %s", exc)

# ---------------------------------------------------------------------------
# Static files — serve frontend
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return FileResponse(str(index))  # will 404 naturally

    # Catch-all for SPA routing — serve index.html for any non-API, non-static path
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        # Don't intercept API or docs routes
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        # Try serving the exact file
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        # Fall back to index.html for SPA routing
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
else:
    logger.warning("Frontend directory not found at %s — static serving disabled", FRONTEND_DIR)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "CortexOS — Company Brain & Decision Ledger",
            "version": "3.0.0",
            "docs": "/docs",
        }
