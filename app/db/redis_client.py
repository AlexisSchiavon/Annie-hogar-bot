"""
Cliente Redis async para sesiones, caché y rate limiting.
"""
import json
from typing import Any

import structlog
from redis.asyncio import Redis, from_url

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# Instancia compartida (se inicializa en startup)
_redis: Redis | None = None


async def get_redis() -> Redis:
    """Retorna la instancia Redis. Crea la conexión si no existe."""
    global _redis
    if _redis is None:
        _redis = await _create_redis()
    return _redis


async def _create_redis() -> Redis:
    client: Redis = await from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    return client


async def close_redis() -> None:
    """Cierra la conexión Redis. Llamar al apagar la app."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def check_redis_health() -> bool:
    """Ping a Redis. Retorna True si está disponible."""
    try:
        client = await get_redis()
        return await client.ping()
    except Exception as exc:
        logger.error("redis_health_check_failed", error=str(exc))
        return False


# ------------------------------------------------------------------
# Helpers de sesión
# ------------------------------------------------------------------

async def get_session(phone: str) -> dict[str, Any] | None:
    """Obtiene la sesión activa de un usuario."""
    client = await get_redis()
    raw = await client.get(f"session:{phone}")
    if raw is None:
        return None
    return json.loads(raw)


async def set_session(phone: str, data: dict[str, Any]) -> None:
    """Guarda/actualiza la sesión de un usuario con TTL."""
    client = await get_redis()
    await client.setex(
        f"session:{phone}",
        settings.session_ttl,
        json.dumps(data, ensure_ascii=False),
    )


async def delete_session(phone: str) -> None:
    """Elimina la sesión de un usuario."""
    client = await get_redis()
    await client.delete(f"session:{phone}")


# ------------------------------------------------------------------
# Helpers de human takeover
# ------------------------------------------------------------------

async def get_human_takeover(phone: str) -> bool:
    """Retorna True si el chat está tomado por un humano."""
    client = await get_redis()
    val = await client.get(f"human_takeover:{phone}")
    return val == "true"


async def set_human_takeover(phone: str, active: bool) -> None:
    """Activa o desactiva el modo human takeover para un teléfono."""
    client = await get_redis()
    if active:
        await client.set(f"human_takeover:{phone}", "true")
    else:
        await client.delete(f"human_takeover:{phone}")


# ------------------------------------------------------------------
# Helpers de catálogo
# ------------------------------------------------------------------

async def get_catalog_cache() -> list[dict] | None:
    """Obtiene el catálogo cacheado. Retorna None si expiró o no existe."""
    client = await get_redis()
    raw = await client.get("catalog:products")
    if raw is None:
        return None
    return json.loads(raw)


async def set_catalog_cache(products: list[dict]) -> None:
    """Cachea el catálogo con TTL de 15 minutos."""
    client = await get_redis()
    pipe = client.pipeline()
    pipe.setex("catalog:products", settings.catalog_ttl, json.dumps(products, ensure_ascii=False))
    pipe.set("catalog:last_refresh", _now_iso())
    await pipe.execute()


async def get_catalog_last_refresh() -> str | None:
    """Retorna el timestamp del último refresh del catálogo."""
    client = await get_redis()
    return await client.get("catalog:last_refresh")


# ------------------------------------------------------------------
# Helpers de bot activo
# ------------------------------------------------------------------

async def is_bot_active() -> bool:
    """Retorna True si el bot está activo globalmente."""
    client = await get_redis()
    val = await client.get("bot:active")
    # Si no hay key, el bot está activo por defecto
    return val != "false"


async def set_bot_active(active: bool) -> None:
    client = await get_redis()
    await client.set("bot:active", "true" if active else "false")


# ------------------------------------------------------------------
# Rate limiting
# ------------------------------------------------------------------

async def check_rate_limit(phone: str) -> bool:
    """
    Retorna True si el usuario NO ha superado el límite.
    Retorna False si debe ser bloqueado.
    """
    client = await get_redis()
    key = f"rate_limit:{phone}"
    count = await client.incr(key)
    if count == 1:
        await client.expire(key, settings.rate_limit_ttl)
    return count <= settings.rate_limit_max


# ------------------------------------------------------------------
# Interno
# ------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
