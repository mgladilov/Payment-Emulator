"""Логирование агентских API-запросов: полное тело запроса и ответа.

Пишет строку в таблицу api_request_log (для окна на странице платежа и для
общей страницы /admin/requests) и дублирует компактно в общий файловый лог.
"""
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import ApiRequestLog

_logger = get_logger("api")


def _pretty(data) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _compact(data) -> str:
    if data is None:
        return "-"
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, default=str)


async def log_api_call(
    session: AsyncSession,
    *,
    endpoint: str,
    method: str,
    path: str,
    status_code: int | None,
    client: str | None = None,
    request_data=None,
    response_data=None,
    payment_id: str | None = None,
    requisite: str | None = None,
) -> None:
    session.add(
        ApiRequestLog(
            endpoint=endpoint,
            method=method,
            path=path,
            status_code=status_code,
            client=client,
            payment_id=payment_id,
            requisite=requisite,
            request_body=_pretty(request_data),
            response_body=_pretty(response_data),
        )
    )
    await session.commit()
    _logger.info(
        "%s %s [%s] client=%s req=%s resp=%s",
        method,
        path,
        status_code,
        client or "-",
        _compact(request_data),
        _compact(response_data),
    )


async def list_for_payment(session: AsyncSession, payment_id: str, limit: int = 200) -> list[ApiRequestLog]:
    rows = (
        await session.scalars(
            select(ApiRequestLog)
            .where(ApiRequestLog.payment_id == payment_id)
            .order_by(ApiRequestLog.timestamp.desc(), ApiRequestLog.id.desc())
            .limit(limit)
        )
    ).all()
    return list(reversed(rows))


async def list_all(
    session: AsyncSession, *, endpoint: str | None = None, q: str | None = None, limit: int = 300
) -> list[ApiRequestLog]:
    stmt = select(ApiRequestLog).order_by(ApiRequestLog.timestamp.desc(), ApiRequestLog.id.desc())
    if endpoint:
        stmt = stmt.where(ApiRequestLog.endpoint == endpoint)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            ApiRequestLog.requisite.like(like)
            | ApiRequestLog.payment_id.like(like)
            | ApiRequestLog.path.like(like)
        )
    return list((await session.scalars(stmt.limit(limit))).all())
