"""
Servicio de resumen diario: genera métricas y las envía a Javier.
"""
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.postgres import get_db_session
from app.models.database import Appointment, Conversation, FollowUp, Lead
from app.models.schemas import DailyMetrics, SummaryResponse

logger = structlog.get_logger(__name__)
settings = get_settings()


class SummaryService:
    async def generate_and_send(self) -> SummaryResponse:
        today = date.today()
        metrics = await self._compute_metrics(today)
        summary_text = self._build_summary_text(metrics, today)

        # Notificar a Javier vía n8n/ManyChat (asíncrono best-effort)
        try:
            await self._notify_javier(summary_text)
        except Exception as exc:
            logger.error("summary_notify_failed", error=str(exc))

        return SummaryResponse(
            summary_text=summary_text,
            metrics=metrics,
            generated_at=datetime.now(timezone.utc),
        )

    async def _compute_metrics(self, today: date) -> DailyMetrics:
        from datetime import timedelta
        start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        async with get_db_session() as session:
            # Nuevos leads hoy
            new_leads = await session.scalar(
                select(func.count(Lead.id)).where(Lead.created_at >= start, Lead.created_at < end)
            ) or 0

            # Citas agendadas hoy
            appointments_scheduled = await session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.created_at >= start,
                    Appointment.created_at < end,
                    Appointment.status == "scheduled",
                )
            ) or 0

            # Citas completadas hoy
            appointments_completed = await session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.scheduled_date == today,
                    Appointment.status == "completed",
                )
            ) or 0

            # Follow-ups enviados hoy
            follow_ups_sent = await session.scalar(
                select(func.count(FollowUp.id)).where(
                    FollowUp.sent_at >= start,
                    FollowUp.sent_at < end,
                )
            ) or 0

            # Mensajes recibidos hoy
            messages_received = await session.scalar(
                select(func.count(Conversation.id)).where(
                    Conversation.role == "user",
                    Conversation.created_at >= start,
                    Conversation.created_at < end,
                )
            ) or 0

            # Leads calificados totales
            qualified_leads = await session.scalar(
                select(func.count(Lead.id)).where(Lead.status == "qualified")
            ) or 0

        return DailyMetrics(
            new_leads=new_leads,
            appointments_scheduled=appointments_scheduled,
            appointments_completed=appointments_completed,
            follow_ups_sent=follow_ups_sent,
            messages_received=messages_received,
            qualified_leads=qualified_leads,
        )

    def _build_summary_text(self, metrics: DailyMetrics, today: date) -> str:
        return (
            f"📊 *Resumen diario {settings.bot_name}* — {today.strftime('%d/%m/%Y')}\n\n"
            f"💬 Mensajes recibidos: {metrics.messages_received}\n"
            f"👤 Nuevos leads: {metrics.new_leads}\n"
            f"⭐ Leads calificados (total): {metrics.qualified_leads}\n"
            f"📅 Citas agendadas hoy: {metrics.appointments_scheduled}\n"
            f"✅ Citas completadas hoy: {metrics.appointments_completed}\n"
            f"🔄 Seguimientos enviados: {metrics.follow_ups_sent}\n\n"
            f"¡Hasta mañana! 🛋️"
        )

    async def _notify_javier(self, summary_text: str) -> None:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{settings.n8n_webhook_url}/webhook/summary-notify",
                json={"phone": settings.javier_phone, "message": summary_text},
                headers={"X-API-Key": settings.api_secret_key},
            )
