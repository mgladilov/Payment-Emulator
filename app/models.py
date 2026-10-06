"""SQLAlchemy-модели эмулятора.

Payment              — созданные платежи (реквизит хранится как есть: это
                       тестовый эмулятор, реальных номеров карт тут нет).
PaymentStatusHistory — полная история переходов статусов (это и есть "лог").
ApiRequestLog        — полный лог агентских API-запросов: тело запроса и наш
                       ответ. Для /pay и /status привязан к payment_id, для
                       /check — нет (платёж не создаётся). Показывается в окне на
                       странице платежа и на общей странице /admin/requests.
PayoutCode           — коды выдачи (PIN из 12 цифр) для эмуляции выдачи наличных:
                       остаток, блокировка точкой, срок действия, принудительный
                       сценарий ответа.
PayoutEvent          — история кода выдачи: генерация, блокировка, выплата
                       (в т.ч. частичная), действия админа.
ScenarioSetting      — настраиваемые задержки по суффиксу реквизита, редактируются
                       через админку без перезапуска приложения.
AdminUser            — админы веб-админки (сессионная авторизация); первый
                       создаётся автоматически, остальные — через админку.
AgentAccount         — учётки агентов для HTTP Basic Auth API; управляются в
                       админке, первая сидится из API_USERNAME/API_PASSWORD.
"""
from datetime import datetime, timezone

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requisite: Mapped[str] = mapped_column(String(64), nullable=False)
    requisite_suffix: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # в минимальных единицах (копейки/центы)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    history: Mapped[list["PaymentStatusHistory"]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
        order_by="PaymentStatusHistory.timestamp, PaymentStatusHistory.id",
    )


class PaymentStatusHistory(Base):
    __tablename__ = "payment_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    payment: Mapped["Payment"] = relationship(back_populates="history")


class ApiRequestLog(Base):
    __tablename__ = "api_request_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # check|pay|status — платежи; payout_check|payout_block|payout_pay — выдачи
    endpoint: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # логин Basic Auth
    # payment_id пуст для /check (платёж не создаётся); заполнен для /pay и /status.
    payment_id: Mapped[str | None] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Реквизит платежа или PIN кода выдачи — по нему ищется лог в админке.
    requisite: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)   # что прислал агент (JSON)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)  # что ответили мы (JSON)


class PayoutCode(Base):
    """Код выдачи. Суммы — целые, в единицах валюты как в API выдач (500 = 500 сом).

    status хранит только рабочее состояние (active | blocked | paid). «Истёк» —
    вычисляемое: active и expires_at в прошлом (см. app.payouts.effective_status).
    """

    __tablename__ = "payout_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pin: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)   # исходная сумма кода
    balance: Mapped[int] = mapped_column(Integer, nullable=False)  # доступно к выдаче сейчас
    currency: Mapped[int] = mapped_column(Integer, nullable=False)  # числовой код валюты из API
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")

    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exchange_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    otp_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("1"))
    commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))

    blocked_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["PayoutEvent"]] = relationship(
        back_populates="code",
        cascade="all, delete-orphan",
        order_by="PayoutEvent.created_at, PayoutEvent.id",
    )


class PayoutEvent(Base):
    __tablename__ = "payout_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_id: Mapped[int] = mapped_column(ForeignKey("payout_codes.id", ondelete="CASCADE"), index=True)
    # created | blocked | paid | partial | admin_unblock | admin_expire | admin_update
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)        # выдано в этом событии
    balance_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_timestamp: Mapped[str | None] = mapped_column(String(64), nullable=True)  # timestamp из /api/pay как есть
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    code: Mapped["PayoutCode"] = relationship(back_populates="events")


class ScenarioSetting(Base):
    __tablename__ = "scenario_settings"

    suffix: Mapped[str] = mapped_column(String(4), primary_key=True)
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentAccount(Base):
    __tablename__ = "agent_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
