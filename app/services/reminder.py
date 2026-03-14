"""
Servicio de recordatorios: envía recordatorio 24h antes de la cita vía ManyChat.
Flujo por cita:
  1. Actualiza custom field "notificacion_cita" del subscriber
  2. Envía la plantilla "recordatorio_cita"
  3. Marca reminder_sent=True en la BD
"""
from datetime import date, timedelta

import httpx
import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.postgres import get_db_session
from app.models.database import Appointment, Lead
from app.models.schemas import ReminderItem

logger = structlog.get_logger(__name__)

_MANYCHAT_SET_FIELD_URL = "https://api.manychat.com/fb/subscriber/setCustomFieldByName"
_MANYCHAT_SEND_CONTENT_URL = "https://api.manychat.com/fb/sending/sendContent"


class ReminderService:
    async def check_and_send(self) -> list[ReminderItem]:
        settings = get_settings()
        tomorrow = date.today() + timedelta(days=1)
        reminders: list[ReminderItem] = []

        async with get_db_session() as session:
            result = await session.execute(
                select(Appointment, Lead)
                .join(Lead, Appointment.lead_id == Lead.id)
                .where(
                    Appointment.scheduled_date == tomorrow,
                    Appointment.status.in_(["scheduled", "confirmed"]),
                    Appointment.reminder_sent.is_(False),
                )
            )
            rows = result.all()

            for appointment, lead in rows:
                sent = await self._send_manychat_reminder(lead, appointment, settings)
                appointment.reminder_sent = True
                logger.info(
                    "reminder_processed",
                    phone=lead.phone,
                    appointment_id=appointment.id,
                    manychat_sent=sent,
                )
                reminders.append(
                    ReminderItem(
                        phone=lead.phone,
                        name=lead.name,
                        message=self._build_notificacion_field(lead.name, appointment),
                        appointment_id=appointment.id,
                        scheduled_date=appointment.scheduled_date,
                        scheduled_time=appointment.scheduled_time,
                    )
                )

        return reminders

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _build_notificacion_field(self, name: str | None, appointment: Appointment) -> str:
        """Construye el valor del custom field notificacion_cita."""
        nombre = name or "Cliente"
        producto = appointment.product_interest or "consulta"
        fecha = appointment.scheduled_date.strftime("%d/%m/%Y")
        hora = appointment.scheduled_time.strftime("%I:%M %p")
        return f"{nombre} - {producto} - {fecha} a las {hora}"

    async def _send_manychat_reminder(
        self,
        lead: Lead,
        appointment: Appointment,
        settings,
    ) -> bool:
        """
        Despacha los dos llamados a ManyChat:
          1. setCustomFieldByName  → "notificacion_cita"
          2. sendContent           → plantilla "recordatorio_cita"
        Retorna True si ambos tuvieron éxito.
        """
        subscriber_id = lead.manychat_subscriber_id
        if not subscriber_id:
            logger.warning("reminder_no_subscriber_id", phone=lead.phone)
            return False

        field_value = self._build_notificacion_field(lead.name, appointment)
        headers = {
            "Authorization": f"Bearer {settings.manychat_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Actualizar custom field
            try:
                resp = await client.post(
                    _MANYCHAT_SET_FIELD_URL,
                    json={
                        "subscriber_id": subscriber_id,
                        "field_name": "notificacion_cita",
                        "field_value": field_value,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("reminder_field_set", subscriber_id=subscriber_id)
            except Exception as exc:
                logger.error("reminder_field_error", subscriber_id=subscriber_id, error=str(exc))
                return False

            # 2. Enviar plantilla
            try:
                resp = await client.post(
                    _MANYCHAT_SEND_CONTENT_URL,
                    json={
                        "subscriber_id": subscriber_id,
                        "data": {
                            "version": "v2",
                            "content": {
                                "type": "whatsapp",
                                "messages": [
                                    {
                                        "type": "template",
                                        "template_name": "recordatorio_cita",
                                        "language": {"code": "es"},
                                        "components": [],
                                    }
                                ],
                            },
                        },
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("reminder_template_sent", subscriber_id=subscriber_id)
            except Exception as exc:
                logger.error("reminder_template_error", subscriber_id=subscriber_id, error=str(exc))
                return False

        return True
