"""API выдачи наличных: /api/v2/check, /api/block, /api/pay. HTTP Basic Auth.

Результат всегда в поле state, HTTP-код всегда 200 (в том числе для ошибок
валидации и внутренних ошибок — {"state": -100}). Логика ответов — в
app.payouts / app.payout_scenarios.
"""
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import api_logs, payouts
from app.api_auth import require_api_auth
from app.database import get_session
from app.logging_config import get_logger
from app.payout_scenarios import CheckState
from app.schemas import PayoutBlockRequest, PayoutCheckRequest, PayoutPayRequest

router = APIRouter(prefix="/api", tags=["payout-api"], dependencies=[Depends(require_api_auth)])

_logger = get_logger("payout")

INTERNAL_ERROR = {"state": int(CheckState.INTERNAL_ERROR)}  # -100 одинаков для всех трёх методов

# path → endpoint для api_request_log (используется и обработчиком ошибок валидации).
ENDPOINTS = {
    "/api/v2/check": "payout_check",
    "/api/block": "payout_block",
    "/api/pay": "payout_pay",
}


async def _handle(
    request: Request,
    session: AsyncSession,
    payload,
    action: Callable[[], Awaitable[dict]],
) -> dict:
    """Выполнить действие, превратить любое исключение в state -100, записать лог."""
    try:
        result = await action()
    except Exception:  # noqa: BLE001 — контракт: внутренняя ошибка = {"state": -100}
        _logger.exception("payout %s failed", request.url.path)
        await session.rollback()
        result = INTERNAL_ERROR
    result = {k: int(v) if k == "state" else v for k, v in result.items()}
    await api_logs.log_api_call(
        session,
        endpoint=ENDPOINTS[request.url.path],
        method="POST",
        path=request.url.path,
        status_code=200,
        client=request.client.host if request.client else None,
        request_data=payload.model_dump(mode="json", by_alias=True),
        response_data=result,
        requisite=payload.pin,
    )
    return result


@router.post("/v2/check")
async def payout_check(
    payload: PayoutCheckRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Проверить код выдачи: сумма к выдаче, получатель, нужен ли OTP. Состояние не меняет."""
    return await _handle(
        request, session, payload, lambda: payouts.check(session, pin=payload.pin, currency=payload.currency)
    )


@router.post("/block")
async def payout_block(
    payload: PayoutBlockRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Заблокировать код за точкой перед выдачей (с проверкой OTP, если нужен)."""
    return await _handle(
        request,
        session,
        payload,
        lambda: payouts.block(session, pin=payload.pin, point_id=payload.point_id, otp=payload.otp),
    )


@router.post("/pay")
async def payout_pay(
    payload: PayoutPayRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Финализировать выдачу: amount — фактически выданная сумма, остаток возвращается на код."""
    return await _handle(
        request,
        session,
        payload,
        lambda: payouts.pay(
            session,
            pin=payload.pin,
            currency=payload.currency,
            amount=payload.amount,
            point_id=payload.point_id,
            client_timestamp=payload.timestamp,
        ),
    )
