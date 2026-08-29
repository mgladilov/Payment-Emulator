"""Агентское API: /check, /pay, /status. Защищено HTTP Basic Auth."""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import holder, payments, scenarios
from app.api_auth import require_api_auth
from app.database import get_session
from app.schemas import (
    CheckRequest,
    CheckResponse,
    PayRequest,
    PayResponse,
    StatusResponse,
)

router = APIRouter(tags=["agent-api"], dependencies=[Depends(require_api_auth)])


@router.post("/check", response_model=CheckResponse)
async def check(payload: CheckRequest) -> CheckResponse:
    """Проверить реквизиты без создания платежа.

    Результат — в поле status ("allowed" | "declined"), HTTP-код всегда 200.
    Отказ (status="declined") отдаётся только для мгновенного decline (суффикс
    0002); отложенный провал (0004) на этапе проверки считается allowed —
    это исход платежа, а не отклонение реквизита.
    """
    sc = scenarios.resolve(payload.requisite)
    suffix = scenarios.suffix_of(payload.requisite)
    declined = sc.initial_status == "failed"
    return CheckResponse(
        status="declined" if declined else "allowed",
        holder_name=holder.holder_name(payload.requisite),
        scenario=sc.key,
        suffix=suffix,
        currency=payload.currency,
        reason="Реквизит будет отклонён провайдером (decline)" if declined else "OK",
    )


@router.post("/pay", response_model=PayResponse, status_code=status.HTTP_201_CREATED)
async def pay(
    payload: PayRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: AsyncSession = Depends(get_session),
) -> PayResponse:
    """Инициировать платёж. Требует заголовок Idempotency-Key.

    Повторный вызов с тем же ключом возвращает тот же платёж, не создавая новый.
    """
    payment, _created = await payments.create_payment(
        session,
        requisite=payload.requisite,
        amount=payload.amount,
        currency=payload.currency,
        idempotency_key=idempotency_key,
    )
    return PayResponse(payment_id=payment.id, status=payment.status, scenario=payment.scenario)


@router.get("/status/{payment_id}", response_model=StatusResponse)
async def get_status(
    payment_id: str,
    session: AsyncSession = Depends(get_session),
) -> StatusResponse:
    """Текущий статус платежа."""
    payment = await payments.get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return StatusResponse(
        payment_id=payment.id,
        status=payment.status,
        scenario=payment.scenario,
        requisite=payment.requisite,
        amount=payment.amount,
        currency=payment.currency,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
    )
