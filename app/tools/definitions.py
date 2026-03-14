"""
Definiciones de herramientas (function calling) para OpenAI.
"""
from typing import Any


def get_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "buscar_producto",
                "description": (
                    "SIEMPRE usa esta tool cuando el cliente pregunte por cualquier "
                    "producto, precio o disponibilidad. Incluye colchones, espaldares, "
                    "bases de cama, salas, comedores, espejos y armarios. "
                    "Nunca respondas sobre productos sin llamar esta tool primero."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Término de búsqueda (ej: 'sofá', 'colchón doble', 'comedor')",
                        },
                        "categoria": {
                            "type": "string",
                            "description": "Filtro opcional por categoría",
                            "enum": ["Colchones", "Alcoba", "Sala", "Comedor", "Espejo", "Sofá cama"],
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agendar_cita",
                "description": (
                    "Agenda una cita para que el cliente visite la tienda. "
                    "Úsala cuando el cliente quiera visitar el almacén o ver los productos en persona."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fecha": {
                            "type": "string",
                            "description": "Fecha de la cita en formato YYYY-MM-DD",
                        },
                        "hora": {
                            "type": "string",
                            "description": "Hora de la cita en formato HH:MM (24h)",
                        },
                        "producto_interes": {
                            "type": "string",
                            "description": "Producto(s) que el cliente quiere ver",
                        },
                    },
                    "required": ["fecha", "hora"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calificar_lead",
                "description": (
                    "Guarda información de calificación del lead cuando se conoce su presupuesto, "
                    "interés principal, urgencia o cualquier dato de cualificación de ventas."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interes": {
                            "type": "string",
                            "description": "Producto o categoría de interés principal",
                        },
                        "presupuesto": {
                            "type": "string",
                            "description": "Rango de presupuesto estimado (ej: '500000-1000000')",
                        },
                        "urgencia": {
                            "type": "string",
                            "description": "Urgencia de compra",
                            "enum": ["inmediata", "esta_semana", "este_mes", "explorando"],
                        },
                        "notas": {
                            "type": "string",
                            "description": "Observaciones adicionales sobre el lead",
                        },
                    },
                    "required": ["interes"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "notificar_javier",
                "description": (
                    "Envía una alerta a Javier (dueño de la tienda) cuando el cliente tiene una "
                    "necesidad urgente, quiere hablar con un humano, o hay una oportunidad de venta importante."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "motivo": {
                            "type": "string",
                            "description": "Razón de la notificación",
                            "enum": ["cliente_urgente", "solicita_humano", "oportunidad_alta", "queja", "otro"],
                        },
                        "detalle": {
                            "type": "string",
                            "description": "Descripción breve de la situación",
                        },
                    },
                    "required": ["motivo", "detalle"],
                },
            },
        },
            {
            "type": "function",
            "function": {
                "name": "compartir_catalogo",
                "description": (
                    "Comparte el link de descarga del catálogo/portafolio PDF de una categoría. "
                    "Úsala cuando el cliente pida el catálogo, portafolio o quiera ver más productos de una categoría."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categoria": {
                            "type": "string",
                            "description": "Categoría del catálogo a compartir",
                            "enum": ["colchones", "camas", "salas", "comedores"],
                        },
                    },
                    "required": ["categoria"],
                },
            },
        },
    ]
