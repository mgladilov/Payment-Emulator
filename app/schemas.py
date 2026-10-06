"""Pydantic-схемы агентского API.

Сумма (amount) — целое число в минимальных единицах валюты (копейки/центы),
чтобы избежать проблем с плавающей точкой. Например, 10000 = 100.00 RUB.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


# --- API выдачи наличных (/api/v2/check, /api/block, /api/pay) -------------
# Поля в camelCase, как в контракте. pin/pointId/otp принимаются и строкой, и
# числом (число приводится к строке). Сумма — целое в единицах валюты (500 = 500
# сом), не в минимальных единицах. Ошибка валидации тела отдаётся как
# {"state": -100} с HTTP 200 (см. обработчик в app.main), а не 422.

class _PayoutBase(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    pin: str = Field(..., max_length=64, description="Код выдачи, 12 цифр")


class PayoutCheckRequest(_PayoutBase):
    currency: int = Field(..., description="Числовой код валюты")
    point_id: str = Field(..., alias="pointId", max_length=64)


class PayoutBlockRequest(_PayoutBase):
    point_id: str = Field(..., alias="pointId", max_length=64)
    otp: str | None = Field(default=None, max_length=16)


class PayoutPayRequest(_PayoutBase):
    timestamp: str = Field(..., max_length=64, description="Время выдачи на терминале")
    currency: int
    amount: int = Field(..., ge=0, description="Сколько фактически выдал терминал")
    point_id: str = Field(..., alias="pointId", max_length=64)
