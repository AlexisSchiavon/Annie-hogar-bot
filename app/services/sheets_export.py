"""
Servicio de exportación de leads a Google Sheets.
Escribe en la hoja "Leads" sin borrar filas existentes —
solo agrega leads cuyo teléfono no esté ya en el Sheet.
"""
import asyncio
from datetime import datetime, timezone

import gspread
import structlog
from google.oauth2.service_account import Credentials

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Leads"
HEADERS = ["telefono", "nombre", "fecha"]


class LeadsExportService:
    async def export(self, leads: list[dict]) -> dict:
        """
        Exporta leads a la hoja 'Leads' del Google Sheet configurado.
        Retorna un dict con total consultado, nuevos agregados y existentes omitidos.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._sync_export, leads)
        return result

    def _sync_export(self, leads: list[dict]) -> dict:
        creds = Credentials.from_service_account_info(
            settings.google_service_account_dict,
            scopes=SCOPES,
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(settings.google_sheets_id)

        # Obtener o crear la hoja "Leads"
        try:
            worksheet = spreadsheet.worksheet(SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=3)
            logger.info("sheets_export_worksheet_created", sheet=SHEET_NAME)

        # Asegurar headers en la primera fila
        existing_values = worksheet.get_all_values()
        if not existing_values:
            worksheet.append_row(HEADERS)
            existing_phones: set[str] = set()
        else:
            # Verificar que la primera fila sea el header correcto
            if existing_values[0] != HEADERS:
                worksheet.insert_row(HEADERS, 1)
                data_rows = existing_values
            else:
                data_rows = existing_values[1:]
            # Columna 0 = telefono
            existing_phones = {str(row[0]).strip() for row in data_rows if row}

        # Filtrar solo leads nuevos
        new_rows = []
        for lead in leads:
            phone = str(lead.get("phone", "")).strip()
            if phone and phone not in existing_phones:
                name = lead.get("name") or ""
                created_at = lead.get("created_at")
                if isinstance(created_at, datetime):
                    fecha = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
                else:
                    fecha = str(created_at or "")
                new_rows.append([phone, name, fecha])

        if new_rows:
            worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
            logger.info("sheets_export_rows_added", count=len(new_rows))
        else:
            logger.info("sheets_export_no_new_leads")

        return {
            "total_consultados": len(leads),
            "nuevos_agregados": len(new_rows),
            "existentes_omitidos": len(leads) - len(new_rows),
        }
