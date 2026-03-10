"""
Motor principal de conversación.
Orquesta: sesión Redis → historial PG → OpenAI GPT → tool calling → respuesta.
"""
from datetime import datetime, timezone

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db.redis_client import (
    check_rate_limit,
    get_session,
    set_session,
)
from app.models.schemas import ChatAction, ChatResponse

logger = structlog.get_logger(__name__)
settings = get_settings()


class ConversationService:
    def __init__(self) -> None:
        self.openai = AsyncOpenAI(api_key=settings.openai_api_key)

    async def process_message(
        self,
        phone: str,
        name: str | None,
        message: str,
        timestamp: datetime,
    ) -> ChatResponse:
        log = logger.bind(phone=phone)

        # Rate limit
        allowed = await check_rate_limit(phone)
        if not allowed:
            log.warning("rate_limit_exceeded")
            return ChatResponse(
                response_text="Estoy recibiendo muchos mensajes. Por favor espera un momento.",
                actions=[],
            )

        # Cargar/crear lead en PG
        lead = await self._upsert_lead(phone, name)

        # Cargar sesión desde Redis (historial reciente)
        session = await get_session(phone) or {"history": [], "lead_id": lead["id"]}

        # Guardar mensaje del usuario en PG
        await self._save_message(lead["id"], "user", message)

        # Construir mensajes para OpenAI
        from app.prompts.system_prompt import build_system_prompt
        system_prompt = await build_system_prompt(lead)
        messages = self._build_messages(system_prompt, session["history"], message)

        # Llamar a OpenAI con tool calling
        response_text, actions, tool_messages = await self._call_openai(messages, lead, phone)

        # Guardar respuesta en PG
        await self._save_message(lead["id"], "assistant", response_text)

        # Actualizar sesión en Redis
        session["history"] = self._truncate_history(
            session["history"] + [
                {"role": "user", "content": message},
                *tool_messages,
                {"role": "assistant", "content": response_text},
            ]
        )
        session["last_message_at"] = timestamp.isoformat()
        await set_session(phone, session)

        return ChatResponse(response_text=response_text, actions=actions)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=settings.openai_retry_base_delay, min=1, max=10),
        reraise=True,
    )
    async def _call_openai(
        self,
        messages: list[dict],
        lead: dict,
        phone: str,
    ) -> tuple[str, list[ChatAction], list[dict]]:
        import json
        from app.tools.definitions import get_tool_definitions
        from app.tools.executor import execute_tool

        tools = get_tool_definitions()
        tool_messages: list[dict] = []
        actions: list[ChatAction] = []

        current_messages = messages.copy()

        # Loop de tool calling (máx 5 rondas para evitar loops infinitos)
        for _ in range(5):
            response = await self.openai.chat.completions.create(
                model=settings.openai_model,
                messages=current_messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=1024,
            )

            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content or "", actions, tool_messages

            if choice.finish_reason == "tool_calls":
                tool_calls = choice.message.tool_calls or []

                # Primero el assistant con tool_calls — los tool results deben ir inmediatamente después
                assistant_tool_msg = choice.message.model_dump(exclude_none=True)
                current_messages.append(assistant_tool_msg)
                tool_messages.append(assistant_tool_msg)

                for tc in tool_calls:
                    tool_result, action = await execute_tool(
                        tc.function.name,
                        json.loads(tc.function.arguments),
                        lead=lead,
                        phone=phone,
                    )
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(tool_result),
                    }
                    current_messages.append(tool_msg)
                    tool_messages.append(tool_msg)
                    if action:
                        actions.append(action)

        # Si agotamos las rondas, retornamos el último contenido disponible
        return choice.message.content or "Disculpa, tuve un problema procesando tu solicitud.", actions, tool_messages

    def _build_messages(
        self,
        system_prompt: str,
        history: list[dict],
        new_message: str,
    ) -> list[dict]:
        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(history)
        msgs.append({"role": "user", "content": new_message})
        return msgs

    def _truncate_history(self, history: list[dict], max_turns: int = 20) -> list[dict]:
        """Mantiene solo los últimos N turnos para no saturar el contexto."""
        truncated = history[-max_turns * 2:]
        # Evitar que el historial empiece con mensajes 'tool' huérfanos (sin assistant tool_calls previo)
        while truncated and truncated[0].get("role") != "user":
            truncated = truncated[1:]
        return truncated

    async def _upsert_lead(self, phone: str, name: str | None) -> dict:
        from sqlalchemy import select
        from app.db.postgres import get_db_session
        from app.models.database import Lead

        async with get_db_session() as session:
            result = await session.execute(select(Lead).where(Lead.phone == phone))
            lead = result.scalar_one_or_none()

            if lead is None:
                lead = Lead(phone=phone, name=name, source="whatsapp")
                session.add(lead)
                await session.flush()
                logger.info("lead_created", phone=phone)
            elif name and not lead.name:
                lead.name = name

            return {"id": lead.id, "phone": lead.phone, "name": lead.name, "status": lead.status, "qualification": lead.qualification}

    async def _save_message(self, lead_id: int, role: str, content: str) -> None:
        from app.db.postgres import get_db_session
        from app.models.database import Conversation

        async with get_db_session() as session:
            msg = Conversation(lead_id=lead_id, role=role, content=content)
            session.add(msg)
