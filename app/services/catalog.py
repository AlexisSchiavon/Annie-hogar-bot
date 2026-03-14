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

    # Sinónimos para términos comunes en el mercado colombiano
    _SYNONYMS: dict[str, list[str]] = {
        "matrimonio": ["doble", "queen", "king", "2 plazas"],
        "doble": ["matrimonio", "queen", "king"],
        "queen": ["doble", "matrimonio"],
        "king": ["doble", "matrimonio", "extra grande"],
        "sencillo": ["individual", "twin", "1 plaza"],
        "individual": ["sencillo", "twin"],
        "twin": ["sencillo", "individual"],
        "colchon": ["colchón"],
        "colchón": ["colchon"],
        "sofa": ["sofá", "sala"],
        "sofá": ["sofa", "sala"],
        "sala": ["sofá", "sofa", "living"],
    }

    async def search_products(self, query: str, category: str | None = None) -> list[CatalogProduct]:
        """Busca productos por nombre, categoría o descripción con matching parcial y sinónimos."""
        products = await self.get_products()
        q = query.lower()

        # Construir conjunto de términos de búsqueda: tokens individuales + sinónimos
        tokens: set[str] = {q}
        for word in q.split():
            tokens.add(word)
            tokens.update(self._SYNONYMS.get(word, []))

        logger.info(
            "search_products_tokens",
            query=query,
            category=category,
            tokens=sorted(tokens),
            pool_total=len(products),
        )

        def matches(p: CatalogProduct) -> bool:
            name = p.name.lower()
            cat = p.category.lower()
            desc = (p.description or "").lower()
            return any(t in name or t in cat or t in desc for t in tokens)

        pool = products
        if category:
            filtered = [p for p in products if category.lower() in p.category.lower()]
            logger.info("search_products_pool_filtered", category=category, pool_size=len(filtered))
            if filtered:
                pool = filtered
            else:
                logger.info("search_products_category_empty_fallback", category=category)

        results = [p for p in pool if matches(p)]
        logger.info("search_products_matches", count=len(results), using_fallback=False)

        # Fallback: sin resultados y sin categoría fija → productos de la categoría más relevante
        if not results and not category:
            matching_cats: list[str] = []
            for p in products:
                if p.category not in matching_cats and any(t in p.category.lower() for t in tokens):
                    matching_cats.append(p.category)

            logger.info(
                "search_products_fallback",
                reason="no_direct_matches",
                matching_categories=matching_cats,
            )

            if matching_cats:
                results = [p for p in products if p.category in matching_cats]
            else:
                results = [p for p in products if p.available]
                logger.info("search_products_fallback_all_available", count=len(results))

        logger.info(
            "search_products_result",
            returned=min(len(results), 10),
            names=[p.name for p in results[:10]],
        )
        return results[:10]  # máximo 10 resultados
