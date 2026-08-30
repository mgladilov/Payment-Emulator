"""Фоновая задача автоперехода статусов.

Опрашивает платежи в статусе `pending` и переводит их в финальный статус
по сценарию, когда истекает задержка. Задержка читается из таблицы
scenario_settings НА КАЖДОЙ ИТЕРАЦИИ — значит правка задержки в админке
действует на лету, в том числе на уже висящие pending-платежи, без перезапуска.

Дедлайн вычисляется как created_at + текущая_задержка(суффикс), а не хранится
в платеже — поэтому изменение задержки сразу меняет момент перехода.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app import scenarios
from app.database import async_session_maker
from app.models import Payment, PaymentStatusHistory, ScenarioSetting, utcnow

logger = logging.getLogger("payment_emulator.background")

POLL_INTERVAL_SECONDS = 1.0


def _as_utc(dt: datetime) -> datetime:
    """SQLite не хранит tzinfo — трактуем наивное время как UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def process_pending_once() -> int:
    """Один проход по pending-платежам. Возвращает число выполненных переходов."""
    transitioned = 0
    async with async_session_maker() as session:
        delays = {
            row.suffix: row.delay_seconds
            for row in (await session.scalars(select(ScenarioSetting))).all()
        }
        now = datetime.now(timezone.utc)

        pending = (
            await session.scalars(select(Payment).where(Payment.status == "pending"))
        ).all()

        for payment in pending:
            sc = scenarios.SCENARIOS.get(payment.requisite_suffix)
            if sc is None or sc.final_status is None:
                continue  # у сценария нет автоперехода — оставляем как есть

            delay = delays.get(payment.requisite_suffix, sc.default_delay)
            deadline = _as_utc(payment.created_at) + timedelta(seconds=delay)
            if now < deadline:
                continue

            # Атомарный переход: обновляем строку только если она всё ещё pending.
            # Гарантирует ровно один переход, даже если случайно запущены два
            # экземпляра (второй получит rowcount=0 и не запишет дубль истории).
            result = await session.execute(
                update(Payment)
                .where(Payment.id == payment.id, Payment.status == "pending")
                .values(status=sc.final_status, updated_at=utcnow())
            )
            if result.rowcount != 1:
                continue  # перевёл кто-то другой — ничего не пишем

            # Пишем историю напрямую (не через payment.history), чтобы не
            # триггерить ленивую подгрузку коллекции в async-контексте.
            session.add(
                PaymentStatusHistory(
                    payment_id=payment.id,
                    status=sc.final_status,
                    note=f"Автопереход pending → {sc.final_status} (задержка {delay}s, сценарий {sc.key})",
                )
            )
            await session.commit()
            logger.info(
                "payment=%s автопереход pending → %s (задержка %ss, сценарий %s)",
                payment.id,
                sc.final_status,
                delay,
                sc.key,
            )
            transitioned += 1

    return transitioned


async def status_transition_loop(stop_event: asyncio.Event) -> None:
    """Крутится в фоне, пока не выставлен stop_event."""
    logger.info("Background status-transition loop started")
    while not stop_event.is_set():
        try:
            await process_pending_once()
        except Exception:  # noqa: BLE001 — фон не должен падать из-за одной итерации
            logger.exception("Error while processing pending payments")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("Background status-transition loop stopped")
