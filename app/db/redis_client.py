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
# Debounce de mensajes entrantes
# ------------------------------------------------------------------
#
# Estructura en Redis:
#   pending:{phone}         → JSON con los mensajes acumulados (sin TTL)
#   debounce_timer:{phone}  → "1" con TTL=debounce_ttl (ventana de espera)
#
# Lógica:
#   - Al llegar un mensaje: append a pending + reset del timer.
#   - Worker cada 1s: para cada pending, si el timer ya expiró → procesar.
# ------------------------------------------------------------------

async def get_pending_message(phone: str) -> dict | None:
    """Retorna los datos de mensajes pendientes para un teléfono, o None si no hay."""
    client = await get_redis()
    raw = await client.get(f"pending:{phone}")
    if raw is None:
        return None
    return json.loads(raw)


async def append_pending_message(phone: str, message: str, name: str | None, debounce_ttl: int) -> None:
    """
    Agrega el mensaje a la cola pendiente del teléfono y reinicia el timer.
    Si no hay cola previa, la crea. Es idempotente para el nombre del lead.
    """
    client = await get_redis()
    raw = await client.get(f"pending:{phone}")
    if raw:
        data = json.loads(raw)
        data["messages"].append(message)
        # Actualizar nombre solo si no teníamos uno
        if name and not data.get("name"):
            data["name"] = name
    else:
        data = {
            "messages": [message],
            "name": name,
            "first_received_at": _now_iso(),
        }

    pipe = client.pipeline()
    pipe.set(f"pending:{phone}", json.dumps(data, ensure_ascii=False))
    pipe.setex(f"debounce_timer:{phone}", debounce_ttl, "1")
    await pipe.execute()


async def delete_pending_message(phone: str) -> None:
    """Elimina la cola pendiente y el timer de un teléfono (tras procesar)."""
    client = await get_redis()
    await client.delete(f"pending:{phone}", f"debounce_timer:{phone}")


async def get_pending_phones() -> list[str]:
    """Retorna todos los teléfonos con mensajes pendientes."""
    client = await get_redis()
    phones: list[str] = []
    async for key in client.scan_iter("pending:*"):
        phones.append(key.removeprefix("pending:"))
    return phones


async def has_debounce_timer(phone: str) -> bool:
    """Retorna True si el timer de debounce aún está activo (ventana abierta)."""
    client = await get_redis()
    return bool(await client.exists(f"debounce_timer:{phone}"))


# ------------------------------------------------------------------
# Interno
# ------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
