"""
Annie Hogar Bot - FastAPI application entrypoint.
Define todos los endpoints y middleware de la aplicación.
"""
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import structlog
from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.postgres import check_postgres_health, dispose_engine
from app.db.redis_client import (
    check_redis_health,
    close_redis,
    get_catalog_last_refresh,
    get_human_takeover,
    get_redis,
    set_human_takeover,
)
from app.dependencies import verify_api_key
from app.models.schemas import (
    CatalogProductsResponse,
    CatalogRefreshResponse,
    ChatRequest,
    ChatResponse,
    FollowUpsResponse,
    HealthResponse,
    RecentLeadsResponse,
    RemindersResponse,
    SummaryResponse,
    TakeoverRequest,
    TakeoverResponse,
)

logger = structlog.get_logger(__name__)
settings = get_settings()


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("annie_bot_starting", bot_name=settings.bot_name)
    redis = await get_redis()
    # Marcar bot como activo al iniciar (si no hay valor previo)
    if not await redis.exists("bot:active"):
        await redis.set("bot:active", "true")
    logger.info("annie_bot_ready")

    yield

    # Shutdown
    logger.info("annie_bot_shutting_down")
    await close_redis()
    await dispose_engine()
    logger.info("annie_bot_stopped")


# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------

app = FastAPI(
    title="Annie Hogar Bot API",
    description="Motor de conversación WhatsApp para Annie Hogar - tienda de muebles y colchones",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/demo")
async def demo():
    return FileResponse("static/index.html")


# ------------------------------------------------------------------
# Middleware: logging de requests
# ------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - start) * 1000
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(elapsed, 2),
    )
    return response


# ------------------------------------------------------------------
# GET /health  (sin auth)
# ------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Sistema"])
async def health_check():
    """Verifica el estado de todos los servicios críticos."""
    pg_ok = await check_postgres_health()
    redis_ok = await check_redis_health()

    # Ping ligero a OpenAI (no consume tokens)
    openai_ok = True
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        await client.models.list()
    except Exception:
        openai_ok = False

    all_ok = pg_ok and redis_ok and openai_ok
    overall = "ok" if all_ok else ("degraded" if (pg_ok or redis_ok) else "down")

    return HealthResponse(
        status=overall,
        postgres=pg_ok,
        redis=redis_ok,
        openai=openai_ok,
    )


# ------------------------------------------------------------------
# POST /chat
# ------------------------------------------------------------------

@app.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Conversación"],
)
async def chat(body: ChatRequest):
    """
    Endpoint principal de conversación.
    Recibe mensaje de WhatsApp (vía n8n/ManyChat) y retorna la respuesta del bot.
    """
    log = logger.bind(phone=body.phone, name=body.name)

    # Verificar human takeover
    takeover_active = await get_human_takeover(body.phone)
    if takeover_active:
        log.info("chat_skipped_human_takeover")
        return ChatResponse(
            response_text="",
            actions=[],
        )

    log.info("chat_received", message_length=len(body.message))

    # Importar servicio de conversación aquí para evitar importaciones circulares
    from app.services.conversation import ConversationService
    service = ConversationService()
    result = await service.process_message(
        phone=body.phone,
        name=body.name,
        message=body.message,
        timestamp=body.timestamp or datetime.now(timezone.utc),
    )

    log.info("chat_responded", actions_count=len(result.actions))
    return result


# ------------------------------------------------------------------
# POST /chat/takeover
# ------------------------------------------------------------------

@app.post(
    "/chat/takeover",
    response_model=TakeoverResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Control"],
)
async def chat_takeover(body: TakeoverRequest):
    """Activa o desactiva el modo human takeover para un número específico."""
    await set_human_takeover(body.phone, body.active)
    status_str = "takeover_active" if body.active else "bot_restored"
    logger.info("takeover_changed", phone=body.phone, active=body.active)
    return TakeoverResponse(ok=True, phone=body.phone, status=status_str)


# ------------------------------------------------------------------
# POST /reminders/check
# ------------------------------------------------------------------

@app.post(
    "/reminders/check",
    response_model=RemindersResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Automatización"],
)
async def check_reminders():
    """
    Disparado por cron de n8n cada hora.
    Revisa citas del día siguiente y envía recordatorios.
    """
    from app.services.reminder import ReminderService
    service = ReminderService()
    reminders = await service.check_and_send()
    logger.info("reminders_checked", count=len(reminders))
    return RemindersResponse(reminders=reminders, count=len(reminders))


# ------------------------------------------------------------------
# POST /followups/check
# ------------------------------------------------------------------

@app.post(
    "/followups/check",
    response_model=FollowUpsResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Automatización"],
)
async def check_followups():
    """
    Disparado por cron de n8n cada 6 horas.
    Revisa leads sin respuesta y envía seguimientos.
    """
    from app.services.followup import FollowUpService
    service = FollowUpService()
    followups = await service.check_and_send()
    logger.info("followups_checked", count=len(followups))
    return FollowUpsResponse(followups=followups, count=len(followups))


# ------------------------------------------------------------------
# POST /summary/daily
# ------------------------------------------------------------------

@app.post(
    "/summary/daily",
    response_model=SummaryResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Automatización"],
)
async def daily_summary():
    """
    Disparado por cron de n8n a las 8pm.
    Genera y envía el resumen diario a Javier.
    """
    from app.services.summary import SummaryService
    service = SummaryService()
    result = await service.generate_and_send()
    logger.info("daily_summary_generated")
    return result


# ------------------------------------------------------------------
# POST /catalog/refresh
# ------------------------------------------------------------------

@app.post(
    "/catalog/refresh",
    response_model=CatalogRefreshResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Catálogo"],
)
async def catalog_refresh():
    """
    Fuerza la recarga del catálogo desde Google Sheets.
    Invalida la caché de Redis y carga los datos frescos.
    """
    from app.services.catalog import CatalogService
    service = CatalogService()
    products = await service.refresh()
    logger.info("catalog_refreshed", count=len(products))
    from app.db.redis_client import get_catalog_last_refresh
    last_updated = await get_catalog_last_refresh() or datetime.now(timezone.utc).isoformat()
    return CatalogRefreshResponse(
        ok=True,
        products_count=len(products),
        last_updated=last_updated,
    )


# ------------------------------------------------------------------
# GET /catalog/products
# ------------------------------------------------------------------

@app.get(
    "/catalog/products",
    response_model=CatalogProductsResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Catálogo"],
)
async def get_catalog():
    """Retorna el catálogo completo (desde caché Redis o Google Sheets)."""
    from app.services.catalog import CatalogService
    service = CatalogService()
    products = await service.get_products()
    last_updated = await get_catalog_last_refresh()
    return CatalogProductsResponse(
        products=products,
        count=len(products),
        last_updated=last_updated,
    )


# ------------------------------------------------------------------
# GET /leads/recent
# ------------------------------------------------------------------

@app.get(
    "/leads/recent",
    response_model=RecentLeadsResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Leads"],
)
async def recent_leads(limit: int = Query(default=10, ge=1, le=100)):
    """Retorna los últimos leads registrados."""
    from sqlalchemy import select, desc
    from app.db.postgres import get_db_session
    from app.models.database import Lead

    async with get_db_session() as session:
        result = await session.execute(
            select(Lead).order_by(desc(Lead.created_at)).limit(limit)
        )
        leads = result.scalars().all()

    return RecentLeadsResponse(
        leads=[LeadOut.model_validate(lead) for lead in leads],
        count=len(leads),
    )


# Importación diferida para evitar error de nombre
from app.models.schemas import LeadOut  # noqa: E402
