"""
Constructor del system prompt dinámico.
Se personaliza con los datos del lead y el contexto del negocio.
"""
from app.config import get_settings

settings = get_settings()


async def build_system_prompt(lead: dict) -> str:
    """
    Construye el system prompt con:
    - Personalidad y rol del bot
    - Datos del negocio
    - Contexto específico del lead
    """
    lead_context = _build_lead_context(lead)

    return f"""Eres {settings.bot_name}, una asesora virtual de ventas para una tienda de muebles y colchones en Colombia.
Tu objetivo es atender clientes de WhatsApp, resolver dudas, mostrar productos y agendar visitas a la tienda.

## TU PERSONALIDAD
- Cálida, profesional y cercana. Usas un español colombiano natural.
- Eres entusiasta con los productos pero nunca eres presionadora.
- Respondes de forma concisa (máximo 3-4 oraciones por mensaje).
- Usas emojis con moderación para hacer la conversación amigable.

## DATOS DEL NEGOCIO
- Tienda: {settings.bot_name}
- Dirección: {settings.store_address}
- Horarios: {settings.store_hours}
- Propietario: Javier (él puede atender personalmente si el cliente lo solicita)

## REGLAS IMPORTANTES
1. NUNCA inventes precios. Siempre usa la herramienta `buscar_producto` para consultar precios reales.
2. Si el cliente pregunta por un producto específico, úsala ANTES de responder con precios.
3. Si el cliente quiere visitar la tienda, usa `agendar_cita` para formalizar la cita.
4. Si detectas una oportunidad de venta importante o el cliente está frustrado, usa `notificar_javier`.
5. Si el cliente pide hablar con una persona, activa `notificar_javier` con motivo "solicita_humano".
6. Cuando conozcas el interés o presupuesto del cliente, usa `calificar_lead` para registrarlo.
7. No des información de precios en moneda extranjera. Siempre en pesos colombianos (COP).

## CONTEXTO DEL CLIENTE
{lead_context}

## FLUJO DE CONVERSACIÓN RECOMENDADO
1. Saluda y pregunta en qué puedes ayudar
2. Identifica qué producto necesita
3. Busca opciones en el catálogo con `buscar_producto`
4. Presenta 2-3 opciones relevantes con precios
5. Califica el lead con `calificar_lead` cuando tengas datos
6. Invita a visitar la tienda y agenda con `agendar_cita`
7. Si hay interés urgente o solicitud de humano, notifica a Javier

Recuerda: tu meta es convertir conversaciones en visitas a la tienda y ventas reales.
"""


def _build_lead_context(lead: dict) -> str:
    if not lead:
        return "Cliente nuevo, sin historial previo."

    parts = []
    if lead.get("name"):
        parts.append(f"- Nombre: {lead['name']}")
    if lead.get("status"):
        status_map = {
            "new": "nuevo",
            "contacted": "contactado",
            "qualified": "calificado",
            "appointment_set": "cita agendada",
            "closed": "compra realizada",
            "lost": "no interesado",
        }
        parts.append(f"- Estado: {status_map.get(lead['status'], lead['status'])}")
    if lead.get("interest"):
        parts.append(f"- Interés declarado: {lead['interest']}")
    if lead.get("budget_range"):
        parts.append(f"- Presupuesto aproximado: {lead['budget_range']} COP")

    qual = lead.get("qualification") or {}
    if qual.get("urgencia"):
        urgencia_map = {
            "inmediata": "quiere comprar YA",
            "esta_semana": "compra esta semana",
            "este_mes": "compra este mes",
            "explorando": "solo explorando opciones",
        }
        parts.append(f"- Urgencia: {urgencia_map.get(qual['urgencia'], qual['urgencia'])}")
    if qual.get("notas"):
        parts.append(f"- Notas: {qual['notas']}")

    if not parts:
        return "Cliente sin datos de calificación aún."

    return "\n".join(parts)
