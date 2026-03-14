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
        # Loguear las columnas disponibles en el Sheet (solo primera fila para no saturar)
        if rows:
            logger.info("catalog_sheet_columns", columns=list(rows[0].keys()))

        products = []
        for row in rows:
            try:
                nombre = str(row.get("nombre", row.get("name", "")))
                categoria = str(row.get("categoria", row.get("category", "General")))
                precio_raw = str(row.get("precio", row.get("price", 0)))
                precio = float(precio_raw.replace(",", "").replace("$", "").replace(".", "").strip() or 0)
                descripcion = str(row.get("descripcion", row.get("description", ""))) or None
                talla = str(row.get("talla", row.get("medida", row.get("size", row.get("dimensión", row.get("dimension", "")))))) or ""

                logger.debug(
                    "catalog_row_parsed",
                    nombre=nombre,
                    talla=talla,
                    precio=precio,
                    categoria=categoria,
                    columnas_extra=list(row.keys()),
                )

                # Si hay talla y no está ya en el nombre, incorporarla al nombre para que la búsqueda funcione
                if talla and talla not in nombre:
                    nombre = f"{nombre} {talla}"

                products.append(
                    CatalogProduct(
                        name=nombre,
                        category=categoria,
                        price=precio,
                        description=descripcion,
                        available=str(row.get("disponible", row.get("available", "si"))).lower() in ("si", "yes", "true", "1"),
                        extra={k: v for k, v in row.items() if k not in ("nombre", "name", "categoria", "category", "precio", "price", "descripcion", "description", "disponible", "available", "talla", "medida", "size", "dimensión", "dimension")},
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

    # Dimensiones canónicas por nombre de talla — para ordenar resultados con talla exacta primero
    _DIMENSION_TOKENS: dict[str, str] = {
        "sencillo": "100x190",
        "individual": "100x190",
        "twin": "100x190",
        "semi": "120x190",
        "semidoble": "120x190",
        "doble": "140x190",
        "matrimonio": "140x190",
        "queen": "160x190",
        "king": "200x200",
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

        # Ordenar: productos cuya descripción/nombre contiene la dimensión exacta solicitada van primero
        dim_token = next(
            (self._DIMENSION_TOKENS[w] for w in q.split() if w in self._DIMENSION_TOKENS),
            None,
        )
        # También detectar dimensiones escritas directamente (ej: "140x190")
        if dim_token is None:
            for word in q.split():
                if "x" in word and word.replace("x", "").replace(".", "").isdigit():
                    dim_token = word
                    break
        if dim_token:
            results.sort(key=lambda p: 0 if dim_token in (p.name + " " + (p.description or "")).lower() else 1)
            logger.info("search_products_dimension_sort", dim_token=dim_token)

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
