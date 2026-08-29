"""SQLAlchemy-модели эмулятора.

Payment              — созданные платежи (реквизит хранится как есть: это
                       тестовый эмулятор, реальных номеров карт тут нет).
PaymentStatusHistory — полная история переходов статусов (это и есть "лог").
ScenarioSetting      — настраиваемые задержки по суффиксу реквизита, редактируются
                       через админку без перезапуска приложения.
AdminUser            — seed-админ(ы) для сессионной авторизации.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
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
    # Момент, когда фоновая задача должна выполнить переход (для отложенных сценариев).
    transition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    history: Mapped[list["PaymentStatusHistory"]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
        order_by="PaymentStatusHistory.timestamp",
    )


class PaymentStatusHistory(Base):
    __tablename__ = "payment_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    payment: Mapped["Payment"] = relationship(back_populates="history")


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
