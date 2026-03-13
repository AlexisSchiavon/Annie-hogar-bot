"""
Configuración centralizada del proyecto.
Todas las variables de entorno se leen aquí via Pydantic Settings.
"""
import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Base de datos
    # ------------------------------------------------------------------
    database_url: str
    postgres_user: str = "annie"
    postgres_password: str = "annie_pass"
    postgres_db: str = "annie"

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_url: str

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"

    # ------------------------------------------------------------------
    # Google
    # ------------------------------------------------------------------
    google_sheets_id: str
    google_drive_folder_id: str
    google_service_account_json: str  # JSON crudo como string

    # ------------------------------------------------------------------
    # ManyChat / WhatsApp
    # ------------------------------------------------------------------
    manychat_api_key: str
    manychat_flow_ns: str

    # ------------------------------------------------------------------
    # Negocio
    # ------------------------------------------------------------------
    javier_phone: str
    bot_name: str = "Geraldine Ruiz"
    store_address: str = "Cra 5ta # 95-14, un kilómetro más abajo de Homecenter, enseguida de Batericars"
    store_hours: str

    # ------------------------------------------------------------------
    # Seguridad
    # ------------------------------------------------------------------
    api_secret_key: str

    # ------------------------------------------------------------------
    # n8n
    # ------------------------------------------------------------------
    n8n_webhook_url: str = "http://n8n:5678"
    # Webhook al que el bot POSTea {phone, response_text, actions} tras el debounce.
    # Si está vacío, la respuesta se descarta (útil en desarrollo sin n8n).
    n8n_chat_response_webhook: str = ""

    # ------------------------------------------------------------------
    # Redis TTLs (segundos)
    # ------------------------------------------------------------------
    session_ttl: int = 7200           # 2 horas
    catalog_ttl: int = 900            # 15 minutos
    rate_limit_ttl: int = 60          # 1 minuto
    rate_limit_max: int = 10          # mensajes por minuto por phone
    debounce_ttl: int = 15            # ventana de debounce en segundos

    # ------------------------------------------------------------------
    # Google Drive — PDFs de catálogo por categoría (IDs de archivo)
    # ------------------------------------------------------------------
    drive_pdf_colchones: str = ""
    drive_pdf_camas: str = ""
    drive_pdf_salas: str = ""
    drive_pdf_comedores: str = ""

    # ------------------------------------------------------------------
    # OpenAI retries
    # ------------------------------------------------------------------
    openai_max_retries: int = 3
    openai_retry_base_delay: float = 1.0  # segundos

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL debe usar driver postgresql+asyncpg://")
        return v

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        if not v.startswith("redis://"):
            raise ValueError("REDIS_URL debe comenzar con redis://")
        return v

    @property
    def google_service_account_dict(self) -> dict[str, Any]:
        """Parsea el JSON del service account desde la variable de entorno."""
        return json.loads(self.google_service_account_json)


@lru_cache
def get_settings() -> Settings:
    """Singleton de configuración. Se cachea para no releer el entorno cada vez."""
    return Settings()
