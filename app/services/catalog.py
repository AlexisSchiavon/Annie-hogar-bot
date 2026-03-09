"""
Servicio de catálogo: lee Google Sheets y cachea en Redis.
"""
import json
from typing import Any

import gspread
import structlog
from google.oauth2.service_account import Credentials

from app.config import get_settings
from app.db.redis_client import get_catalog_cache, set_catalog_cache
from app.models.schemas import CatalogProduct

logger = structlog.get_logger(__name__)
settings = get_settings()

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


class CatalogService:
    async def get_products(self) -> list[CatalogProduct]:
        """Retorna productos desde caché Redis o refresca desde Google Sheets."""
        cached = await get_catalog_cache()
        if cached is not None:
            logger.debug("catalog_from_cache", count=len(cached))
            return [CatalogProduct(**p) for p in cached]
        return await self.refresh()

    async def refresh(self) -> list[CatalogProduct]:
        """Fuerza recarga desde Google Sheets y actualiza caché."""
        logger.info("catalog_refresh_start")
        raw_rows = await self._fetch_from_sheets()
        products = self._parse_rows(raw_rows)
        await set_catalog_cache([p.model_dump() for p in products])
        logger.info("catalog_refresh_done", count=len(products))
        return products

    async def _fetch_from_sheets(self) -> list[dict[str, Any]]:
        """Lee todas las filas de la hoja de cálculo."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_fetch)

    def _sync_fetch(self) -> list[dict[str, Any]]:
        creds = Credentials.from_service_account_info(
            settings.google_service_account_dict,
            scopes=SCOPES,
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(settings.google_sheets_id).sheet1
        return sheet.get_all_records()

    def _parse_rows(self, rows: list[dict[str, Any]]) -> list[CatalogProduct]:
        products = []
        for row in rows:
            try:
                products.append(
                    CatalogProduct(
                        name=str(row.get("nombre", row.get("name", ""))),
                        category=str(row.get("categoria", row.get("category", "General"))),
                        price=float(str(row.get("precio", row.get("price", 0))).replace(",", "").replace("$", "")),
                        description=str(row.get("descripcion", row.get("description", ""))) or None,
                        available=str(row.get("disponible", row.get("available", "si"))).lower() in ("si", "yes", "true", "1"),
                        extra={k: v for k, v in row.items() if k not in ("nombre", "name", "categoria", "category", "precio", "price", "descripcion", "description", "disponible", "available")},
                    )
                )
            except Exception as exc:
                logger.warning("catalog_row_parse_error", row=row, error=str(exc))
        return products

    async def search_products(self, query: str, category: str | None = None) -> list[CatalogProduct]:
        """Busca productos por nombre o categoría (para tool calling)."""
        products = await self.get_products()
        q = query.lower()
        results = [
            p for p in products
            if q in p.name.lower() or (p.description and q in p.description.lower())
        ]
        if category:
            results = [p for p in results if p.category.lower() == category.lower()]
        return results[:10]  # máximo 10 resultados
