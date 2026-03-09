"""
Templates de mensajes reutilizables para el bot.
Todos los textos salientes pasan por aquí — ningún servicio
debe construir strings de mensaje ad-hoc.
"""
from datetime import date, time


# ------------------------------------------------------------------
# Helpers internos
# ------------------------------------------------------------------

def _greeting(name: str | None) -> str:
    return f"Hola {name}! 👋" if name else "Hola! 👋"


def _fmt_date(d: date) -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{dias[d.weekday()]} {d.day} de {meses[d.month - 1]}"


def _fmt_time(t: time) -> str:
    hour = t.hour % 12 or 12
    minute = f"{t.minute:02d}"
    period = "am" if t.hour < 12 else "pm"
    return f"{hour}:{minute} {period}"


# ------------------------------------------------------------------
# 1. Saludo — nuevo contacto
# ------------------------------------------------------------------

def welcome_new(*, name: str | None, bot_name: str, store_hours: str) -> str:
    """
    Primer mensaje al lead. Cálido, breve, genera confianza.
    """
    return (
        f"{_greeting(name)}\n\n"
        f"Bienvenido/a a *{bot_name}* 🛋️\n"
        f"Soy Annie, tu asesora virtual. Estoy aquí para ayudarte a encontrar "
        f"los muebles y colchones perfectos para tu hogar.\n\n"
        f"¿En qué te puedo ayudar hoy? Puedo mostrarte nuestro catálogo, "
        f"darte información de precios o agendarte una visita a la tienda.\n\n"
        f"🕐 Horarios: {store_hours}"
    )


# ------------------------------------------------------------------
# 2. Saludo — contacto recurrente
# ------------------------------------------------------------------

def welcome_returning(*, name: str | None, bot_name: str) -> str:
    """
    Mensaje de bienvenida cuando el lead ya había interactuado antes.
    """
    return (
        f"{_greeting(name)}\n\n"
        f"Qué bueno verte de nuevo en *{bot_name}*. 😊\n"
        f"¿En qué te puedo ayudar hoy? ¿Seguimos con lo que estabas buscando "
        f"o tienes una nueva consulta?"
    )


# ------------------------------------------------------------------
# 3. Seguimientos automáticos
# ------------------------------------------------------------------

def followup_attempt_1(*, name: str | None, bot_name: str) -> str:
    """Primer seguimiento — 24 horas sin respuesta."""
    return (
        f"{_greeting(name)}\n\n"
        f"Soy Annie de *{bot_name}*. 😊\n"
        f"Quería saber si pudiste pensar en los muebles o colchones que te interesaban. "
        f"Estoy aquí para resolver cualquier duda. ¿Te ayudo con algo?"
    )


def followup_attempt_2(*, name: str | None, bot_name: str) -> str:
    """Segundo seguimiento — 72 horas sin respuesta."""
    return (
        f"{_greeting(name)}\n\n"
        f"Te escribimos desde *{bot_name}* 🛋️\n"
        f"Esta semana tenemos opciones increíbles en muebles y colchones. "
        f"¿Te gustaría que te cuente sobre las novedades o alguna promoción especial?"
    )


def followup_attempt_3(*, name: str | None, bot_name: str) -> str:
    """Tercer y último seguimiento — 7 días sin respuesta."""
    return (
        f"{_greeting(name)}\n\n"
        f"Este es nuestro último mensaje desde *{bot_name}*. 🙂\n"
        f"Cuando estés listo/a para renovar tu hogar, con gusto te asesoramos. "
        f"¡Que tengas un excelente día! 🌟"
    )


def followup_by_attempt(attempt: int, *, name: str | None, bot_name: str) -> str:
    """Despacha el template correcto según el número de intento."""
    handlers = {
        1: followup_attempt_1,
        2: followup_attempt_2,
        3: followup_attempt_3,
    }
    fn = handlers.get(attempt, followup_attempt_1)
    return fn(name=name, bot_name=bot_name)


# ------------------------------------------------------------------
# 4. Recordatorio de cita
# ------------------------------------------------------------------

def appointment_reminder(
    *,
    name: str | None,
    bot_name: str,
    store_address: str,
    scheduled_date: date,
    scheduled_time: time,
    product_interest: str | None = None,
) -> str:
    """Recordatorio enviado 24h antes de la cita agendada."""
    product_line = f"\n🛒 Productos a ver: {product_interest}" if product_interest else ""
    return (
        f"{_greeting(name)}\n\n"
        f"Te recordamos que mañana tienes una cita en *{bot_name}* 📅\n\n"
        f"📆 {_fmt_date(scheduled_date).capitalize()}\n"
        f"🕐 {_fmt_time(scheduled_time)}\n"
        f"📍 {store_address}"
        f"{product_line}\n\n"
        f"¿Confirmas tu asistencia? Responde *SÍ* para confirmar o *NO* para cancelar."
    )


def appointment_confirmation(
    *,
    name: str | None,
    bot_name: str,
    store_address: str,
    scheduled_date: date,
    scheduled_time: time,
) -> str:
    """Confirmación inmediata tras agendar la cita en la conversación."""
    return (
        f"¡Perfecto{', ' + name if name else ''}! 🎉\n\n"
        f"Tu cita ha quedado agendada:\n"
        f"📆 {_fmt_date(scheduled_date).capitalize()}\n"
        f"🕐 {_fmt_time(scheduled_time)}\n"
        f"📍 {store_address}\n\n"
        f"Te enviaremos un recordatorio el día anterior. ¡Te esperamos en *{bot_name}*! 🛋️"
    )


# ------------------------------------------------------------------
# 5. Resumen diario (para Javier)
# ------------------------------------------------------------------

def daily_summary(
    *,
    bot_name: str,
    report_date: date,
    new_leads: int,
    messages_received: int,
    qualified_leads: int,
    appointments_scheduled: int,
    appointments_completed: int,
    follow_ups_sent: int,
) -> str:
    """Resumen diario enviado a Javier a las 8pm."""
    return (
        f"📊 *Resumen diario — {bot_name}*\n"
        f"_{_fmt_date(report_date).capitalize()}_\n\n"
        f"💬 Mensajes recibidos: *{messages_received}*\n"
        f"👤 Nuevos leads: *{new_leads}*\n"
        f"⭐ Leads calificados (total): *{qualified_leads}*\n"
        f"📅 Citas agendadas hoy: *{appointments_scheduled}*\n"
        f"✅ Citas completadas hoy: *{appointments_completed}*\n"
        f"🔄 Seguimientos enviados: *{follow_ups_sent}*\n\n"
        f"¡Hasta mañana! 🛋️"
    )


# ------------------------------------------------------------------
# 6. Alertas a Javier (notificaciones internas)
# ------------------------------------------------------------------

def javier_alert_new_appointment(
    *,
    client_name: str | None,
    client_phone: str,
    scheduled_date: date,
    scheduled_time: time,
    product_interest: str | None,
) -> str:
    product_line = f"\n🛒 Interés: {product_interest}" if product_interest else ""
    name_str = client_name or client_phone
    return (
        f"📅 *Nueva cita agendada*\n\n"
        f"👤 Cliente: {name_str}\n"
        f"📱 Tel: {client_phone}\n"
        f"📆 {_fmt_date(scheduled_date).capitalize()} a las {_fmt_time(scheduled_time)}"
        f"{product_line}"
    )


def javier_alert_hot_lead(
    *,
    client_name: str | None,
    client_phone: str,
    interest: str,
    budget: str | None,
    detail: str,
) -> str:
    budget_line = f"\n💰 Presupuesto: {budget}" if budget else ""
    name_str = client_name or client_phone
    return (
        f"🔥 *Lead caliente detectado*\n\n"
        f"👤 {name_str} ({client_phone})\n"
        f"🛋️ Interés: {interest}"
        f"{budget_line}\n"
        f"💬 {detail}"
    )


def javier_alert_human_requested(
    *,
    client_name: str | None,
    client_phone: str,
    last_message: str,
) -> str:
    name_str = client_name or client_phone
    return (
        f"🙋 *Cliente solicita atención humana*\n\n"
        f"👤 {name_str}\n"
        f"📱 {client_phone}\n\n"
        f"Último mensaje:\n_{last_message}_\n\n"
        f"Responde directamente al número para tomar el chat."
    )


def javier_alert_generic(
    *,
    motivo: str,
    client_name: str | None,
    client_phone: str,
    detail: str,
) -> str:
    name_str = client_name or client_phone
    motivo_map = {
        "cliente_urgente": "⚡ Cliente urgente",
        "oportunidad_alta": "💎 Oportunidad de venta alta",
        "queja": "⚠️ Queja de cliente",
        "otro": "ℹ️ Aviso del bot",
    }
    titulo = motivo_map.get(motivo, "ℹ️ Aviso")
    return (
        f"{titulo}\n\n"
        f"👤 {name_str} ({client_phone})\n"
        f"💬 {detail}"
    )
