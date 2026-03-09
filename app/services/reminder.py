"""
Servicio de recordatorios: envía recordatorio 24h antes de la cita.
"""
from datetime import date, timedelta

import structlog
from sqlalchemy import select

from app.db.postgres import get_db_session
from app.models.database import Appointment, Lead
from app.models.schemas import ReminderItem

logger = structlog.get_logger(__name__)


class ReminderService:
    async def check_and_send(self) -> list[ReminderItem]:
        """
        Busca citas programadas para mañana que no tengan recordatorio enviado.
        Retorna lista de recordatorios para que n8n los despache vía ManyChat.
        """
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
                message = self._build_reminder_message(lead.name, appointment)
                reminders.append(
                    ReminderItem(
                        phone=lead.phone,
                        name=lead.name,
                        message=message,
                        appointment_id=appointment.id,
                        scheduled_date=appointment.scheduled_date,
                        scheduled_time=appointment.scheduled_time,
                    )
                )
                appointment.reminder_sent = True
                logger.info("reminder_queued", phone=lead.phone, appointment_id=appointment.id)

        return reminders

    def _build_reminder_message(self, name: str | None, appointment: Appointment) -> str:
        from app.config import get_settings
        from app.prompts.templates import appointment_reminder
        settings = get_settings()
        return appointment_reminder(
            name=name,
            bot_name=settings.bot_name,
            store_address=settings.store_address,
            scheduled_date=appointment.scheduled_date,
            scheduled_time=appointment.scheduled_time,
            product_interest=appointment.product_interest,
        )
