"""
Servicio de seguimientos automáticos a leads sin respuesta.
"""
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from app.db.postgres import get_db_session
from app.models.database import Conversation, FollowUp, Lead
from app.models.schemas import FollowUpItem

logger = structlog.get_logger(__name__)

# Configuración de seguimientos
FOLLOWUP_SCHEDULE = [
    (24, 1, "primer seguimiento"),
    (72, 2, "segundo seguimiento"),
    (168, 3, "tercer seguimiento"),  # 7 días
]


class FollowUpService:
    async def check_and_send(self) -> list[FollowUpItem]:
        """
        Revisa leads calificados que no han respondido y genera seguimientos.
        Retorna la lista para que n8n los despache.
        """
        now = datetime.now(timezone.utc)
        followups: list[FollowUpItem] = []

        async with get_db_session() as session:
            # Leads calificados o contactados, sin human takeover
            result = await session.execute(
                select(Lead).where(
                    Lead.status.in_(["new", "contacted", "qualified"]),
                    Lead.human_takeover.is_(False),
                )
            )
            leads = result.scalars().all()

            for lead in leads:
                followup = await self._evaluate_lead(session, lead, now)
                if followup:
                    followups.append(followup)

        return followups

    async def _evaluate_lead(self, session, lead: Lead, now: datetime) -> FollowUpItem | None:
        # Último mensaje del lead
        last_msg_result = await session.execute(
            select(Conversation.created_at)
            .where(Conversation.lead_id == lead.id, Conversation.role == "user")
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        last_msg_at = last_msg_result.scalar_one_or_none()
        if last_msg_at is None:
            return None

        # Asegurar timezone aware
        if last_msg_at.tzinfo is None:
            last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

        hours_silent = (now - last_msg_at).total_seconds() / 3600

        # Intentos ya realizados
        attempts_result = await session.execute(
            select(func.max(FollowUp.attempt_number)).where(FollowUp.lead_id == lead.id)
        )
        max_attempt = attempts_result.scalar_one_or_none() or 0

        for hours_threshold, attempt_num, label in FOLLOWUP_SCHEDULE:
            if hours_silent >= hours_threshold and max_attempt < attempt_num:
                message = self._build_followup_message(lead.name, attempt_num)
                followup_record = FollowUp(lead_id=lead.id, attempt_number=attempt_num)
                session.add(followup_record)
                logger.info("followup_queued", phone=lead.phone, attempt=attempt_num)
                return FollowUpItem(
                    phone=lead.phone,
                    name=lead.name,
                    message=message,
                    attempt=attempt_num,
                    lead_id=lead.id,
                )

        return None

    def _build_followup_message(self, name: str | None, attempt: int) -> str:
        from app.config import get_settings
        from app.prompts.templates import followup_by_attempt
        settings = get_settings()
        return followup_by_attempt(attempt, name=name, bot_name=settings.bot_name)
