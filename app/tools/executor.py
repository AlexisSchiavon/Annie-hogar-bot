"""
Ejecutor de herramientas OpenAI.
Cada función tool mapea a lógica de negocio real.
"""
from datetime import date, datetime, time
from typing import Any

import structlog

from app.models.schemas import ChatAction

logger = structlog.get_logger(__name__)


async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    lead: dict,
    phone: str,
) -> tuple[Any, ChatAction | None]:
    """
    Ejecuta una herramienta por nombre y retorna (resultado, acción_opcional).
    El resultado se pasa de vuelta a OpenAI como tool result.
    La acción (si existe) se incluye en el ChatResponse para n8n.
    """
    handlers = {
        "buscar_producto": _buscar_producto,
        "agendar_cita": _agendar_cita,
        "calificar_lead": _calificar_lead,
        "notificar_javier": _notificar_javier,
        "compartir_catalogo": _compartir_catalogo,
    }

    handler = handlers.get(tool_name)
    if handler is None:
        logger.warning("unknown_tool", tool=tool_name)
        return f"Herramienta '{tool_name}' no encontrada.", None

    try:
        return await handler(arguments, lead=lead, phone=phone)
    except Exception as exc:
        logger.error("tool_execution_error", tool=tool_name, error=str(exc))
        return f"Error ejecutando {tool_name}: {str(exc)}", None


async def _buscar_producto(args: dict, *, lead: dict, phone: str) -> tuple[str, None]:
    from app.services.catalog import CatalogService
    service = CatalogService()
    results = await service.search_products(
        query=args["query"],
        category=args.get("categoria"),
    )
    if not results:
        return "No encontré productos que coincidan con tu búsqueda.", None

    lines = [f"Productos encontrados ({len(results)}):"]
    for p in results:
        availability = "✅ Disponible" if p.available else "❌ No disponible"
        price_fmt = f"${p.price:,.0f}".replace(",", ".")
        lines.append(f"- {p.name} | {p.category} | {price_fmt} COP | {availability}")
        if p.description:
            lines.append(f"  {p.description}")

    return "\n".join(lines), None


async def _agendar_cita(args: dict, *, lead: dict, phone: str) -> tuple[str, ChatAction | None]:
    from app.db.postgres import get_db_session
    from app.models.database import Appointment

    try:
        scheduled_date = date.fromisoformat(args["fecha"])
        hora_parts = args["hora"].split(":")
        scheduled_time = time(int(hora_parts[0]), int(hora_parts[1]))
    except (ValueError, IndexError) as exc:
        return f"Fecha u hora inválida: {exc}", None

    async with get_db_session() as session:
        appointment = Appointment(
            lead_id=lead["id"],
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            product_interest=args.get("producto_interes"),
            status="scheduled",
        )
        session.add(appointment)
        await session.flush()
        appt_id = appointment.id

    logger.info("appointment_created", lead_id=lead["id"], date=str(scheduled_date), appt_id=appt_id)

    action = ChatAction(
        type="notify_javier",
        data={
            "event": "appointment_scheduled",
            "phone": phone,
            "name": lead.get("name"),
            "date": args["fecha"],
            "time": args["hora"],
            "product": args.get("producto_interes", ""),
            "appointment_id": appt_id,
        },
    )

    fecha_str = scheduled_date.strftime("%d/%m/%Y")
    hora_str = scheduled_time.strftime("%I:%M %p")
    return (
        f"Cita agendada: {fecha_str} a las {hora_str}. "
        f"Te llegará una confirmación. ¡Te esperamos!"
    ), action


async def _calificar_lead(args: dict, *, lead: dict, phone: str) -> tuple[str, None]:
    from sqlalchemy import select
    from app.db.postgres import get_db_session
    from app.models.database import Lead

    async with get_db_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead["id"]))
        db_lead = result.scalar_one_or_none()
        if db_lead:
            db_lead.interest = args.get("interes", db_lead.interest)
            db_lead.budget_range = args.get("presupuesto", db_lead.budget_range)
            db_lead.status = "qualified"
            current_qual = db_lead.qualification or {}
            current_qual.update({k: v for k, v in args.items() if k not in ("interes", "presupuesto")})
            db_lead.qualification = current_qual

    logger.info("lead_qualified", lead_id=lead["id"], interest=args.get("interes"))
    return "Lead calificado correctamente.", None


async def _notificar_javier(args: dict, *, lead: dict, phone: str) -> tuple[str, ChatAction]:
    from app.services.notifications import NotificationService

    motivo = args["motivo"]
    detalle = args["detalle"]

    svc = NotificationService()

    if motivo == "solicita_humano":
        payload = await svc.human_requested(
            client_name=lead.get("name"),
            client_phone=phone,
            last_message=detalle,
            lead_id=lead.get("id"),
        )
    else:
        payload = await svc.generic_alert(
            motivo=motivo,
            client_name=lead.get("name"),
            client_phone=phone,
            detail=detalle,
            lead_id=lead.get("id"),
        )

    # Despacho best-effort (no bloquea la respuesta al usuario)
    import asyncio
    asyncio.ensure_future(svc.dispatch(payload))

    action = ChatAction(type="notify_javier", data=payload)
    logger.info("javier_notified", motivo=motivo, phone=phone)
    return "Notificación enviada a Javier.", action

async def _compartir_catalogo(args: dict, *, lead: dict, phone: str) -> tuple[str, None]:
    from app.config import get_settings
    settings = get_settings()

    categoria = args["categoria"].lower()
    id_map = {
        "colchones": settings.drive_pdf_colchones,
        "camas": settings.drive_pdf_camas,
        "salas": settings.drive_pdf_salas,
        "comedores": settings.drive_pdf_comedores,
    }
    file_id = id_map.get(categoria, "")
    if not file_id:
        return f"El catálogo de {categoria} no está disponible por el momento.", None

    link = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
    return f"Aquí tienes el portafolio de {categoria}: {link}", None
