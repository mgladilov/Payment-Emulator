"""Бизнес-логика платежей: создание с идемпотентностью, запись истории.

Логика сценариев не хардкодится здесь — берётся из app.scenarios.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import scenarios
from app.models import Payment, PaymentStatusHistory


async def get_by_idempotency_key(session: AsyncSession, key: str) -> Payment | None:
    return await session.scalar(select(Payment).where(Payment.idempotency_key == key))


async def get_payment(session: AsyncSession, payment_id: str) -> Payment | None:
    return await session.get(Payment, payment_id)


async def create_payment(
    session: AsyncSession,
    *,
    requisite: str,
    amount: int,
    currency: str,
    idempotency_key: str,
) -> tuple[Payment, bool]:
    """Создать платёж по сценарию реквизита.

    Возвращает (payment, created): created=False, если платёж с таким
    Idempotency-Key уже существовал (идемпотентный повтор).
    """
    existing = await get_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing, False

    sc = scenarios.resolve(requisite)
    suffix = scenarios.suffix_of(requisite)

    payment = Payment(
        id=str(uuid.uuid4()),
        requisite=requisite,
        requisite_suffix=suffix,
        amount=amount,
        currency=currency,
        status=sc.initial_status,
        idempotency_key=idempotency_key,
        scenario=sc.key,
    )
    # Первый шаг истории — приём платежа (это и есть ответ /pay: "accepted").
    payment.history.append(
        PaymentStatusHistory(status=scenarios.ACCEPTED_STATUS, note="Платёж принят (/pay)")
    )
    # Второй шаг — исход по сценарию: либо сразу финальный, либо pending в ожидании.
    note = (
        "Мгновенный исход по сценарию"
        if sc.initial_status in scenarios.FINAL_STATUSES
        else "Ожидает автоперехода (pending)"
    )
    payment.history.append(PaymentStatusHistory(status=sc.initial_status, note=note))
    session.add(payment)

    try:
        await session.commit()
    except IntegrityError:
        # Гонка по idempotency_key: кто-то создал такой же платёж параллельно.
        await session.rollback()
        existing = await get_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing, False
        raise

    await session.refresh(payment)
    return payment, True
