"""FastAPI application: API under /api, dashboard (static frontend) under /.

Run:  .venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload
"""
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import PROJECT_ROOT, get_settings
from backend.database import check_connection, init_db
from backend.routers import ai, contracts, ingestion, pipeline, validation
from backend.services import pipeline_service
from backend.services.errors import ServiceError
from backend.utils.logging_config import configure_logging

log = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    init_db()
    recovered = pipeline_service.recover_interrupted_runs()
    log.info("application started", extra={"database": settings.safe_database_target,
                                           "llm_provider": settings.llm_provider,
                                           "interrupted_runs_recovered": recovered})
    yield


app = FastAPI(
    title="Data Contract Validation Platform",
    version="1.0.0",
    description="Contracts, schema-change detection, record validation, quarantine and idempotent publishing.",
    lifespan=lifespan,
)


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    """Make browsers revalidate dashboard files, so UI updates show without a hard refresh."""
    response = await call_next(request)
    if request.url.path.startswith("/ui/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    body = {"detail": exc.message}
    if exc.details is not None:
        body["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Request validation failed", "errors": errors})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # Never leak stack traces or connection strings to the client; log them instead.
    error_id = uuid.uuid4().hex[:12]
    log.error("unhandled error", exc_info=exc, extra={"error_id": error_id, "path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_id": error_id})


@app.get("/api/health", tags=["system"])
def health():
    settings = get_settings()
    try:
        version = check_connection()
        db = {"status": "up", "mysql_version": version, "target": settings.safe_database_target}
    except Exception:  # noqa: BLE001
        log.exception("database health check failed")
        db = {"status": "down", "target": settings.safe_database_target}
    return {"status": "ok" if db["status"] == "up" else "degraded", "database": db,
            "llm_provider": settings.llm_provider}


for r in (contracts.router, ingestion.router, pipeline.router, validation.router, ai.router):
    app.include_router(r)

FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/index.html")


if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
