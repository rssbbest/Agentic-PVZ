"""
app/main.py  –  FastAPI application entry point
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from app.config import INSECURE_DEFAULT_SECRET, settings
from app.database import engine, open_pool, AsyncSessionLocal
from app.models import Base
from app.routers import (
    competition, submissions, runs, leaderboard, leaderboards, websocket, enemy_configs, game_api,
)
from app.routers.enemy_configs import seed_enemy_configs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("autopilot.backend")


# ── Lifespan: create tables on startup ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AutoPilot backend services...")
    for name in ("SIMULATOR_SECRET", "ADMIN_SECRET"):
        if getattr(settings, name) == INSECURE_DEFAULT_SECRET:
            logger.warning("%s is the insecure default – set it before running the event", name)
    # Auto-create tables (dev convenience; use Alembic for production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all adds missing tables, never missing columns: bring databases
        # created before the cost feature up to date.
        await conn.execute(text("ALTER TABLE enemy_configs ADD COLUMN IF NOT EXISTS budget INTEGER"))
        for column in ("password_hash VARCHAR(200)", "approved BOOLEAN NOT NULL DEFAULT true",
                       "banned BOOLEAN NOT NULL DEFAULT false", "profile JSONB"):
            await conn.execute(text(f"ALTER TABLE teams ADD COLUMN IF NOT EXISTS {column}"))
        await conn.execute(text("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS board VARCHAR(10) NOT NULL DEFAULT 'private'"))
    logger.info("Database tables verified.")
    # Seed the 50 test enemy configs and default teams (no-op if already present)
    async with AsyncSessionLocal() as db:
        await seed_enemy_configs(db)
        from app.services.competition import seed_test_cases
        await seed_test_cases(db)
    await open_pool()
    logger.info("AutoPilot backend initialized and ready.")
    yield
    await engine.dispose()
    logger.info("Database engine disposed.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Zombie Apocalypse: Agentic Event – Backend",
    description=(
        "Orchestrates SDK validation, simulator dispatch, evaluator scoring "
        "and leaderboard storage for the Zombie Defence competition."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Allow the simulator / frontend origins
# The game shows `error` from a failed reply to the player (Unity API guide), so
# every error body carries a readable `error`; `detail` stays for the dashboard.
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    text = exc.detail if isinstance(exc.detail, str) else "Request refused"
    return JSONResponse({"error": text, "detail": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _invalid_request(request: Request, exc: RequestValidationError):
    first = (exc.errors() or [{}])[0]
    where = ".".join(str(x) for x in first.get("loc", ()) if x != "body")
    return JSONResponse({"error": f"Invalid request: {where} {first.get('msg', '')}".strip(),
                         "detail": jsonable_errors(exc)}, status_code=422)


def jsonable_errors(exc: RequestValidationError) -> list:
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(exc.errors())


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception):
    logging.getLogger("app").exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Server error: please try again", "detail": "Internal Server Error"},
                        status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Logging Middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.perf_counter()
    method = request.method
    path = request.url.path
    query = request.url.query
    query_str = f"?{query}" if query else ""
    client_host = request.client.host if request.client else "unknown"

    logger.info("--> %s %s%s from %s", method, path, query_str, client_host)
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info("<-- %s %s%s [%d] (%.2fms)", method, path, query_str, response.status_code, duration_ms)
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("ERR %s %s%s (%.2fms): %s", method, path, query_str, duration_ms, exc)
        raise

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(submissions.teams_router)       # POST /teams
app.include_router(submissions.router)             # POST/GET /submissions, POST /submissions/{id}/retry
app.include_router(enemy_configs.router)           # GET/POST /enemy_configs
app.include_router(runs.router)                    # GET/POST /runs/...
app.include_router(leaderboard.router)             # GET /leaderboard
app.include_router(websocket.router)               # WS  /runs/{id}/stream
app.include_router(game_api.router)                # POST /api/team, POST /api/results
app.include_router(competition.router)             # login, register, test cases, eval, /register, /admin
app.include_router(competition.admin)              # /admin/api/* (X-Admin-Secret)
app.include_router(leaderboards.router)            # /api/leaderboard/{public,private,stream}
app.include_router(leaderboards.admin)             # /admin/api/leaderboard


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
