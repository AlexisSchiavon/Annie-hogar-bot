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
    set_human_takeover,
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
        subscriber_id: str | None = None,
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

        # Detectar imagen — activar human takeover y notificar a Javier
        if self._is_image_message(message):
            log.info("image_received_takeover_activated")
            await set_human_takeover(phone, True)
            import asyncio
            from app.services.notifications import NotificationService
            asyncio.ensure_future(
                NotificationService().imagen_recibida(
                    client_name=name,
                    client_phone=phone,
                )
            )
            return ChatResponse(
                response_text="Vi que nos enviaste una imagen 😊 En un momento un asesor de Annie Hogar te atiende personalmente.",
                actions=[],
            )

        # Transcribir nota de voz si el mensaje es una URL de audio
        if self._is_voice_message(message):
            transcribed = await self._transcribe_voice(message, log)
            if transcribed is None:
                return ChatResponse(
                    response_text="No pude escuchar tu nota de voz. ¿Puedes escribir tu mensaje por favor? 😊",
                    actions=[],
                )
            message = f"[Nota de voz]: {transcribed}"

        # Cargar/crear lead en PG
        lead = await self._upsert_lead(phone, name, subscriber_id)

        # Cargar sesión desde Redis; si expiró, reconstruir historial desde PostgreSQL
        session = await get_session(phone)
        if session is None:
            history = await self._load_history_from_pg(lead["id"])
            session = {"history": history, "lead_id": lead["id"]}
            if history:
                log.info("session_rebuilt_from_pg", messages=len(history))

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

    async def _load_history_from_pg(self, lead_id: int, limit: int = 6) -> list[dict]:
        """Reconstruye el historial de conversación desde PostgreSQL cuando la sesión Redis expiró."""
        from sqlalchemy import select
        from app.db.postgres import get_db_session
        from app.models.database import Conversation

        async with get_db_session() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.lead_id == lead_id)
                .where(Conversation.role.in_(["user", "assistant"]))
                .order_by(Conversation.created_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
        # Revertir a orden cronológico
        return [{"role": r.role, "content": r.content} for r in reversed(rows)]

    async def _upsert_lead(self, phone: str, name: str | None, subscriber_id: str | None = None) -> dict:
        from sqlalchemy import select
        from app.db.postgres import get_db_session
        from app.models.database import Lead

        async with get_db_session() as session:
            result = await session.execute(select(Lead).where(Lead.phone == phone))
            lead = result.scalar_one_or_none()

            if lead is None:
                lead = Lead(
                    phone=phone,
                    name=name,
                    source="whatsapp",
                    manychat_subscriber_id=subscriber_id,
                )
                session.add(lead)
                await session.flush()
                logger.info("lead_created", phone=phone)
            else:
                if name and not lead.name:
                    lead.name = name
                if subscriber_id and lead.manychat_subscriber_id != subscriber_id:
                    lead.manychat_subscriber_id = subscriber_id

            return {"id": lead.id, "phone": lead.phone, "name": lead.name, "status": lead.status, "qualification": lead.qualification}

    async def _save_message(self, lead_id: int, role: str, content: str) -> None:
        from app.db.postgres import get_db_session
        from app.models.database import Conversation

        async with get_db_session() as session:
            msg = Conversation(lead_id=lead_id, role=role, content=content)
            session.add(msg)

    _IMAGE_DOMAINS = ("mmg.whatsapp.net", "pps.whatsapp.net")
    _IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

    def _is_image_message(self, message: str) -> bool:
        """Detecta si el mensaje es una URL de imagen de WhatsApp."""
        msg = message.strip()
        if not msg.startswith("https://"):
            return False
        lower = msg.lower().split("?")[0]
        return any(d in lower for d in self._IMAGE_DOMAINS) or any(lower.endswith(ext) for ext in self._IMAGE_EXTENSIONS)

    _AUDIO_EXTENSIONS = (".ogg", ".mp3", ".m4a", ".opus")

    def _is_voice_message(self, message: str) -> bool:
        """Detecta si el mensaje es una URL apuntando a un archivo de audio."""
        msg = message.strip()
        if not msg.startswith("https://"):
            return False
        lower = msg.lower().split("?")[0]  # ignorar query params al chequear extensión
        return "voice" in lower or any(lower.endswith(ext) for ext in self._AUDIO_EXTENSIONS)

    async def _transcribe_voice(self, url: str, log) -> str | None:
        """Descarga el audio desde la URL y lo transcribe con Whisper. Retorna None si falla."""
        import io
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
                audio_bytes = response.content
                content_type = response.headers.get("content-type", "")

            # Verificar que sea audio si no lo detectamos por URL
            if not content_type.startswith("audio/") and not self._is_voice_message(url):
                log.warning("voice_non_audio_content_type", content_type=content_type)
                return None

            # Determinar nombre de archivo para que Whisper infiera el formato
            lower_url = url.lower().split("?")[0]
            ext = next((e for e in self._AUDIO_EXTENSIONS if lower_url.endswith(e)), ".ogg")
            filename = f"voice{ext}"

            audio_file = (filename, io.BytesIO(audio_bytes), content_type or "audio/ogg")
            transcript = await self.openai.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="es",
            )
            text = transcript.text.strip()
            log.info("voice_transcribed", chars=len(text))
            return text

        except Exception as exc:
            log.error("voice_transcription_failed", error=str(exc))
            return None
