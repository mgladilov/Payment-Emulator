"""Pydantic-схемы агентского API.

Сумма (amount) — целое число в минимальных единицах валюты (копейки/центы),
чтобы избежать проблем с плавающей точкой. Например, 10000 = 100.00 RUB.
"""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


class CheckRequest(BaseModel):
    requisite: str = Field(..., min_length=1, max_length=64, description="Номер карты/счёта")
    amount: int = Field(..., gt=0, description="Сумма в минимальных единицах (копейки/центы)")
    currency: str = Field(default="RUB", min_length=3, max_length=3)

    @field_validator("requisite")
    @classmethod
    def requisite_has_enough_digits(cls, v: str) -> str:
        if len(_digits(v)) < 4:
            raise ValueError("requisite must contain at least 4 digits")
        return v


class CheckResponse(BaseModel):
    # Результат проверки передаётся строковым статусом, а не HTTP-кодом:
    # ответ всегда 200, а решение — в поле status ("allowed" | "declined").
    status: str
    holder_name: str  # ФИО держателя (сгенерировано детерминированно по реквизиту)
    scenario: str
    suffix: str
    currency: str  # эхо валюты из запроса
    reason: str


class PayRequest(CheckRequest):
    """Тело /pay: реквизиты + ключ идемпотентности (передаётся в теле, не в заголовке)."""

    idempotency_key: str = Field(
        ..., min_length=1, max_length=128, description="Ключ идемпотентности; повтор с тем же ключом возвращает тот же платёж"
    )


class PayResponse(BaseModel):
    payment_id: str
    status: str
    scenario: str


class StatusResponse(BaseModel):
    payment_id: str
    status: str
    scenario: str
    requisite: str
    amount: int
    currency: str
    created_at: datetime
    updated_at: datetime
