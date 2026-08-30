"""Логирование агентских API-запросов: полное тело запроса и ответа.

Пишет строку в таблицу api_request_log (для окна на странице платежа и для
общей страницы /admin/requests) и дублирует компактно в общий файловый лог.
"""
import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import ApiRequestLog

_logger = get_logger("api")

# Ретеншен: держим только последние RETENTION записей, чистку запускаем не на
# каждый вызов, а раз в PRUNE_EVERY вставок (дешёвый DELETE по диапазону PK).
RETENTION = 5000
PRUNE_EVERY = 200


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


async def _maybe_prune(session: AsyncSession, latest_id: int | None) -> None:
    """Изредка подрезать таблицу до RETENTION последних записей."""
    if latest_id is None or latest_id % PRUNE_EVERY != 0:
        return
    cutoff = latest_id - RETENTION
    if cutoff <= 0:
        return
    await session.execute(delete(ApiRequestLog).where(ApiRequestLog.id <= cutoff))
    await session.commit()


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
    # Общий файловый лог пишем всегда — это durable-канал, он не должен зависеть
    # от успеха записи в БД.
    _logger.info(
        "%s %s [%s] client=%s req=%s resp=%s",
        method,
        path,
        status_code,
        client or "-",
        _compact(request_data),
        _compact(response_data),
    )
    row = ApiRequestLog(
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
    session.add(row)
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 — логирование не должно ронять сам запрос
        await session.rollback()
        _logger.warning("Не удалось сохранить api-лог для %s %s", method, path, exc_info=True)
        return
    await _maybe_prune(session, row.id)


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
