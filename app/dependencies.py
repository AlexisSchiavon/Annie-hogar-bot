"""
Dependency injection centralizada para FastAPI.
Importar desde aquí en todos los routers/endpoints.
"""
from collections.abc import AsyncGenerator

import structlog
from fastapi import Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.postgres import get_db as _get_db
from app.db.redis_client import get_redis as _get_redis

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Autenticación
# ------------------------------------------------------------------

async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Valida el header X-API-Key contra API_SECRET_KEY.
    Raise 401 si es inválida o está ausente.
    """
    if x_api_key != settings.api_secret_key:
        logger.warning("invalid_api_key_attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida o ausente.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# Alias para uso en dependencies=[Depends(...)]
RequireApiKey = Depends(verify_api_key)


# ------------------------------------------------------------------
# Base de datos
# ------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provee una AsyncSession de PostgreSQL por request.
    Hace commit automático al finalizar; rollback si hay excepción.

    Uso:
        @app.post("/ruta")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async for session in _get_db():
        yield session


# ------------------------------------------------------------------
# Redis
# ------------------------------------------------------------------

async def get_redis_dep() -> Redis:
    """
    Provee la instancia Redis compartida.

    Uso:
        @app.post("/ruta")
        async def endpoint(redis: Redis = Depends(get_redis_dep)):
            ...
    """
    return await _get_redis()


# ------------------------------------------------------------------
# Settings (re-exportado para consistencia)
# ------------------------------------------------------------------

def get_app_settings() -> Settings:
    """
    Re-exporta get_settings() como dependency injectable.

    Uso:
        async def endpoint(cfg: Settings = Depends(get_app_settings)):
            ...
    """
    return get_settings()


# ------------------------------------------------------------------
# Dependencia compuesta: DB + Auth
# ------------------------------------------------------------------

class AuthenticatedDB:
    """
    Dependency compuesta que garantiza autenticación y provee DB session.
    Útil para endpoints que siempre necesitan ambas cosas.

    Uso:
        @app.post("/ruta")
        async def endpoint(ctx: AuthenticatedDB = Depends()):
            await ctx.db.execute(...)
    """
    def __init__(
        self,
        db: AsyncSession = Depends(get_db),
        _: None = Depends(verify_api_key),
    ) -> None:
        self.db = db
