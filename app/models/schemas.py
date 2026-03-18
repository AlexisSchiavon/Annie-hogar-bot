"""
Pydantic schemas para request/response de todos los endpoints FastAPI.
"""
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------------
# Comunes
# ------------------------------------------------------------------

class OkResponse(BaseModel):
    ok: bool = True
    message: str = "ok"


# ------------------------------------------------------------------
# /chat
# ------------------------------------------------------------------

class ChatAction(BaseModel):
    """Acción adicional a ejecutar tras la respuesta (ej. trigger ManyChat flow)."""
    type: str = Field(..., description="Tipo de acción: send_flow, tag_lead, notify_javier, etc.")
    data: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    phone: str | None = Field(None, max_length=50, description="Número de teléfono E.164 (opcional)")
    name: str | None = Field(None, max_length=100)
    message: str = Field(..., min_length=1, max_length=4096)
    timestamp: datetime | None = Field(None)
    subscriber_id: str | None = Field(None, description="ID del suscriptor en ManyChat")
    instant: bool = Field(
        False,
        description="Si True, salta el debounce y procesa el mensaje inmediatamente (útil para demos).",
    )


class ChatResponse(BaseModel):
    response_text: str
    actions: list[ChatAction] = Field(default_factory=list)


class ChatAckResponse(BaseModel):
    """Respuesta inmediata cuando el mensaje entra en la cola de debounce."""
    status: Literal["received"]
    debounce_seconds: int


# ------------------------------------------------------------------
# /chat/takeover
# ------------------------------------------------------------------

class TakeoverRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    active: bool

    @field_validator("phone")
    @classmethod
    def sanitize_phone(cls, v: str) -> str:
        return "".join(c for c in v if c.isdigit())


class TakeoverResponse(BaseModel):
    ok: bool = True
    phone: str
    status: Literal["takeover_active", "bot_restored"]


# ------------------------------------------------------------------
# /reminders/check
# ------------------------------------------------------------------

class ReminderItem(BaseModel):
    phone: str
    name: str | None
    message: str
    appointment_id: int
    scheduled_date: date
    scheduled_time: time


class RemindersResponse(BaseModel):
    reminders: list[ReminderItem]
    count: int


# ------------------------------------------------------------------
# /followups/check
# ------------------------------------------------------------------

class FollowUpItem(BaseModel):
    phone: str
    name: str | None
    message: str
    attempt: int
    lead_id: int


class FollowUpsResponse(BaseModel):
    followups: list[FollowUpItem]
    count: int


# ------------------------------------------------------------------
# /summary/daily
# ------------------------------------------------------------------

class DailyMetrics(BaseModel):
    new_leads: int = 0
    appointments_scheduled: int = 0
    appointments_completed: int = 0
    follow_ups_sent: int = 0
    messages_received: int = 0
    qualified_leads: int = 0


class SummaryResponse(BaseModel):
    summary_text: str
    metrics: DailyMetrics
    generated_at: datetime


# ------------------------------------------------------------------
# /catalog/refresh  &  /catalog/products
# ------------------------------------------------------------------

class CatalogProduct(BaseModel):
    name: str
    category: str
    price: float
    description: str | None = None
    available: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class CatalogRefreshResponse(BaseModel):
    ok: bool = True
    products_count: int
    last_updated: str


class CatalogProductsResponse(BaseModel):
    products: list[CatalogProduct]
    count: int
    last_updated: str | None


# ------------------------------------------------------------------
# /health
# ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    postgres: bool
    redis: bool
    openai: bool
    version: str = "1.0.0"


# ------------------------------------------------------------------
# /leads/recent
# ------------------------------------------------------------------

class LeadOut(BaseModel):
    id: int
    phone: str
    name: str | None
    source: str
    status: str
    interest: str | None
    budget_range: str | None
    human_takeover: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecentLeadsResponse(BaseModel):
    leads: list[LeadOut]
    count: int
