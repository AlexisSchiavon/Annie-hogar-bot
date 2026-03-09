"""
Conexión async a PostgreSQL con SQLAlchemy.
Provee engine, session factory y helpers de query.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# Motor async compartido por toda la app
engine = create_async_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=False,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos SQLAlchemy."""
    pass


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager que provee una sesión async.
    Hace commit automático si no hay excepciones; rollback si las hay.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection para FastAPI.
    Uso: session: AsyncSession = Depends(get_db)
    """
    async with get_db_session() as session:
        yield session


async def check_postgres_health() -> bool:
    """Ping a la base de datos. Retorna True si está disponible."""
    try:
        from sqlalchemy import text
        async with get_db_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("postgres_health_check_failed", error=str(exc))
        return False


async def dispose_engine() -> None:
    """Cierra todas las conexiones del pool. Llamar al apagar la app."""
    await engine.dispose()
