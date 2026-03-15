"""
Servicio de notificaciones a Javier.
Construye payloads para que n8n los despache por ManyChat/WhatsApp.
No envía directamente — retorna el payload para que el orquestador decida.
"""
from datetime import date, time
from typing import Any, Literal

import structlog

from app.config import get_settings
from app.prompts.templates import (
    javier_alert_generic,
    javier_alert_hot_lead,
    javier_alert_human_requested,
    javier_alert_new_appointment,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

NotificationEvent = Literal[
    "appointment_scheduled",
    "hot_lead",
    "human_requested",
    "cliente_urgente",
    "oportunidad_alta",
    "queja",
    "otro",
    "imagen_recibida",
]


def _base_payload(message: str, event: NotificationEvent) -> dict[str, Any]:
    """Estructura base del payload que n8n recibirá y reenviará a Javier."""
    return {
        "recipient_phone": settings.javier_phone,
        "message": message,
        "event": event,
        "channel": "whatsapp",
    }


class NotificationService:
    """
    Construye y (opcionalmente) despacha notificaciones a Javier.

    Cada método retorna un dict listo para enviarse como body a un
    webhook de n8n. El campo `message` contiene el texto formateado
    para WhatsApp (soporta *negrita* y _cursiva_ de WhatsApp).
    """

    # ------------------------------------------------------------------
    # Nueva cita agendada
    # ------------------------------------------------------------------

    async def new_appointment(
        self,
        *,
        client_name: str | None,
        client_phone: str,
        scheduled_date: date,
        scheduled_time: time,
        product_interest: str | None = None,
        appointment_id: int | None = None,
    ) -> dict[str, Any]:
        message = javier_alert_new_appointment(
            client_name=client_name,
            client_phone=client_phone,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            product_interest=product_interest,
        )
        payload = _base_payload(message, "appointment_scheduled")
        payload["metadata"] = {
            "client_phone": client_phone,
            "appointment_id": appointment_id,
        }
        logger.info("notification_built", notification_event="appointment_scheduled", client=client_phone)
        return payload

    # ------------------------------------------------------------------
    # Lead caliente detectado
    # ------------------------------------------------------------------

    async def hot_lead(
        self,
        *,
        client_name: str | None,
        client_phone: str,
        interest: str,
        budget: str | None = None,
        detail: str,
        lead_id: int | None = None,
    ) -> dict[str, Any]:
        message = javier_alert_hot_lead(
            client_name=client_name,
            client_phone=client_phone,
            interest=interest,
            budget=budget,
            detail=detail,
        )
        payload = _base_payload(message, "hot_lead")
        payload["metadata"] = {
            "client_phone": client_phone,
            "lead_id": lead_id,
            "interest": interest,
        }
        logger.info("notification_built", notification_event="hot_lead", client=client_phone)
        return payload

    # ------------------------------------------------------------------
    # Cliente solicita intervención humana
    # ------------------------------------------------------------------

    async def human_requested(
        self,
        *,
        client_name: str | None,
        client_phone: str,
        last_message: str,
        lead_id: int | None = None,
    ) -> dict[str, Any]:
        message = javier_alert_human_requested(
            client_name=client_name,
            client_phone=client_phone,
            last_message=last_message,
        )
        payload = _base_payload(message, "human_requested")
        payload["metadata"] = {
            "client_phone": client_phone,
            "lead_id": lead_id,
            "action": "enable_takeover",  # señal para el workflow de n8n
        }
        logger.info("notification_built", notification_event="human_requested", client=client_phone)
        return payload

    # ------------------------------------------------------------------
    # Alerta genérica (urgente, queja, oportunidad, etc.)
    # ------------------------------------------------------------------

    async def generic_alert(
        self,
        *,
        motivo: NotificationEvent,
        client_name: str | None,
        client_phone: str,
        detail: str,
        lead_id: int | None = None,
    ) -> dict[str, Any]:
        message = javier_alert_generic(
            motivo=motivo,
            client_name=client_name,
            client_phone=client_phone,
            detail=detail,
        )
        payload = _base_payload(message, motivo)
        payload["metadata"] = {
            "client_phone": client_phone,
            "lead_id": lead_id,
        }
        logger.info("notification_built", notification_event=motivo, client=client_phone)
        return payload

    # ------------------------------------------------------------------
    # Imagen recibida: notifica a Javier directamente vía ManyChat
    # ------------------------------------------------------------------

    async def imagen_recibida(
        self,
        *,
        client_name: str | None,
        client_phone: str,
    ) -> bool:
        """
        Notifica a Javier directamente vía ManyChat cuando un cliente envía una imagen.
        1. Actualiza el custom field notificacion_cita de Javier
        2. Envía la plantilla cliente_agenda (fallback: recordatorio_cita)
        """
        import httpx

        subscriber_id = settings.manychat_javier_subscriber_id
        nombre = client_name or "Cliente"
        field_value = f"📸 {nombre} ({client_phone}) envió una imagen y espera atención personal."

        headers = {
            "Authorization": f"Bearer {settings.manychat_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Actualizar custom field notificacion_cita de Javier
            try:
                resp = await client.post(
                    "https://api.manychat.com/fb/subscriber/setCustomFieldByName",
                    json={
                        "subscriber_id": subscriber_id,
                        "field_name": "notificacion_cita",
                        "field_value": field_value,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("imagen_recibida_field_set", subscriber_id=subscriber_id)
            except Exception as exc:
                logger.error("imagen_recibida_field_error", error=str(exc))
                return False

            # 2. Enviar plantilla — intenta cliente_agenda, fallback recordatorio_cita
            for template_name in ("cliente_agenda", "recordatorio_cita"):
                try:
                    resp = await client.post(
                        "https://api.manychat.com/fb/sending/sendContent",
                        json={
                            "subscriber_id": subscriber_id,
                            "data": {
                                "version": "v2",
                                "content": {
                                    "type": "whatsapp",
                                    "messages": [
                                        {
                                            "type": "template",
                                            "template_name": template_name,
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
                    logger.info("imagen_recibida_template_sent", template=template_name)
                    return True
                except Exception as exc:
                    logger.warning(
                        "imagen_recibida_template_failed",
                        template=template_name,
                        error=str(exc),
                    )

        logger.error("imagen_recibida_all_templates_failed", client_phone=client_phone)
        return False

    # ------------------------------------------------------------------
    # Dispatch: envía el payload al webhook de n8n
    # ------------------------------------------------------------------

    async def dispatch(self, payload: dict[str, Any]) -> bool:
        """
        Envía el payload al webhook de notificaciones en n8n.
        Retorna True si n8n respondió 2xx, False si falló (best-effort).
        """
        import httpx

        url = f"{settings.n8n_webhook_url}/webhook/notify-javier"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"X-API-Key": settings.api_secret_key},
                )
                resp.raise_for_status()
                logger.info(
                    "notification_dispatched",
                    notification_event=payload.get("event"),
                    status=resp.status_code,
                )
                return True
        except Exception as exc:
            logger.error(
                "notification_dispatch_failed",
                notification_event=payload.get("event"),
                error=str(exc),
            )
            return False

    # ------------------------------------------------------------------
    # Helpers de alto nivel: construye Y despacha en un paso
    # ------------------------------------------------------------------

    async def send_new_appointment(self, **kwargs) -> bool:
        payload = await self.new_appointment(**kwargs)
        return await self.dispatch(payload)

    async def send_hot_lead(self, **kwargs) -> bool:
        payload = await self.hot_lead(**kwargs)
        return await self.dispatch(payload)

    async def send_human_requested(self, **kwargs) -> bool:
        payload = await self.human_requested(**kwargs)
        return await self.dispatch(payload)

    async def send_generic_alert(self, **kwargs) -> bool:
        payload = await self.generic_alert(**kwargs)
        return await self.dispatch(payload)
