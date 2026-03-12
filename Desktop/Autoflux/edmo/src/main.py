import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings

# Logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    # Pre-cache TTS filler phrases (needed for BOTH EVI and legacy pipelines)
    # Fillers play instantly while AI thinks — must be ready at startup
    from src.services.tts_service import pre_cache_fillers
    logger.info("Pre-caching TTS filler phrases...")
    try:
        await pre_cache_fillers()
        logger.info("Filler pre-cache complete")
    except Exception as e:
        logger.warning("Filler pre-cache failed (non-fatal): %s", e)

    # Register EVI tools for Salesforce integration
    if settings.use_evi:
        from src.services.evi_tools import register_all_tools
        logger.info("Registering Hume EVI tools...")
        register_all_tools()

    yield

    # Cleanup EVI sessions on shutdown
    if settings.use_evi:
        from src.services import hume_evi_service
        for meeting_id in list(hume_evi_service._sessions.keys()):
            await hume_evi_service.close_session(meeting_id)


app = FastAPI(
    title="Meeting Intelligence Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

from src.routes.bot_media import router as bot_media_router
from src.routes.meetings import router as meetings_router
from src.routes.salesforce import router as salesforce_router
from src.routes.websocket import router as websocket_router
from src.webhooks.recall_webhooks import router as recall_webhooks_router

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routers (must be registered before static file mount)
app.include_router(meetings_router)
app.include_router(salesforce_router)
app.include_router(websocket_router)
app.include_router(recall_webhooks_router)
app.include_router(bot_media_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mi-engine"}


# Serve React frontend (SPA) — mounted last so API routes take priority
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIR.is_dir():
    # Serve /assets/* directly
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="static")

    # SPA fallback: any non-API GET that doesn't match a route → index.html
    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc: Exception):
        # Only serve SPA for browser navigation (not API calls)
        if request.url.path.startswith(("/api/", "/ws/", "/docs", "/redoc", "/openapi.json", "/health", "/bot-media")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(FRONTEND_DIR / "index.html")
