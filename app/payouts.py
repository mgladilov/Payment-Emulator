"""Бизнес-логика кодов выдачи: генерация, check → block → pay, действия админа.

Жизненный цикл кода:
    active --block(otp)--> blocked --pay(amount)--> paid      (выдана вся сумма)
                                    \\-pay(amount)--> active   (частичная выдача:
                                                               остаток вернулся на код)
"Истёк" не хранится — вычисляется по expires_at (см. effective_status).

/api/block и /api/pay берут строку кода под SELECT ... FOR UPDATE: параллельные
запросы по одному PIN выполняются строго по очереди, двойной блокировки или
двойного списания быть не может.

Функции API возвращают готовое тело ответа (dict с полем state); коды state и
принудительные сценарии — в app.payout_scenarios.
"""
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import payout_scenarios as ps
from app.models import PayoutCode, PayoutEvent, utcnow

PIN_LENGTH = 12


def generate_pin() -> str:
    """Случайный PIN из 12 цифр. Первая цифра не 0 — чтобы клиент, хранящий PIN
    числом, не терял ведущие нули."""
    return str(secrets.randbelow(9) + 1) + "".join(
        str(secrets.randbelow(10)) for _ in range(PIN_LENGTH - 1)
    )


def is_valid_pin(pin: str) -> bool:
    return len(pin) == PIN_LENGTH and pin.isdigit()


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_expired(code: PayoutCode, now: datetime | None = None) -> bool:
    return (now or utcnow()) >= _as_utc(code.expires_at)


def effective_status(code: PayoutCode, now: datetime | None = None) -> str:
    """Статус для показа: active с истёкшим сроком отображается как expired.
    Заблокированный код не «истекает» — точка уже начала выдачу."""
    if code.status == ps.STATUS_ACTIVE and is_expired(code, now):
        return ps.STATUS_EXPIRED
    return code.status


def _num(value: Decimal) -> int | float:
    """Decimal → число для JSON: 5 вместо 5.0, 87.4 вместо "87.4000"."""
    return int(value) if value == value.to_integral_value() else float(value)


async def get_by_pin(session: AsyncSession, pin: str, *, for_update: bool = False) -> PayoutCode | None:
    if not is_valid_pin(pin):
        return None
    stmt = select(PayoutCode).where(PayoutCode.pin == pin)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


# --- Генерация ------------------------------------------------------------

async def create_codes(
    session: AsyncSession,
    *,
    count: int,
    amount: int,
    currency: int,
    ttl_hours: int,
    scenario: str = ps.DEFAULT_SCENARIO,
    otp_needed: bool = True,
    phone: str | None = None,
    fio: str | None = None,
    exchange_id: int | None = None,
    rate: Decimal = Decimal("1"),
    commission: Decimal = Decimal("0"),
) -> list[PayoutCode]:
    """Сгенерировать count кодов с одинаковыми параметрами.

    Коллизия PIN (вероятность ничтожна при 9·10^11 вариантов) ловится
    уникальным индексом — тогда пачка перегенерируется.
    """
    if scenario not in ps.SCENARIOS:
        raise ValueError(f"unknown payout scenario: {scenario}")
    for _attempt in range(3):
        now = utcnow()
        codes = []
        for _ in range(count):
            code = PayoutCode(
                pin=generate_pin(),
                amount=amount,
                balance=amount,
                currency=currency,
                status=ps.STATUS_ACTIVE,
                scenario=scenario,
                phone=phone or None,
                fio=fio or None,
                exchange_id=exchange_id,
                otp_needed=otp_needed,
                rate=rate,
                commission=commission,
                expires_at=now + timedelta(hours=ttl_hours),
            )
            code.events.append(
                PayoutEvent(event="created", balance_after=amount, note=f"Сгенерирован, сценарий {scenario}")
            )
            codes.append(code)
        session.add_all(codes)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        return codes
    raise RuntimeError("could not generate unique payout pins")


# --- API выдачи -----------------------------------------------------------

async def check(session: AsyncSession, *, pin: str, currency: int) -> dict:
    """/api/v2/check: информация о коде, без изменения состояния."""
    code = await get_by_pin(session, pin)
    if code is None or code.currency != currency:
        return {"state": ps.CheckState.NOT_FOUND}
    forced = ps.resolve(code.scenario).check
    if forced is not None:
        return {"state": forced}
    if code.status == ps.STATUS_PAID:
        return {"state": ps.CheckState.ALREADY_PAID}
    if effective_status(code) == ps.STATUS_EXPIRED:
        return {"state": ps.CheckState.EXPIRED}
    return {
        "state": ps.CheckState.SUCCESS,
        "amount": code.balance,
        "phone": code.phone,
        "fio": code.fio,
        "exchangeId": code.exchange_id,
        "otpNeeded": code.otp_needed,
        "rate": _num(code.rate),
        "commission": _num(code.commission),
    }


async def block(session: AsyncSession, *, pin: str, point_id: str, otp: str | None) -> dict:
    """/api/block: заблокировать код за точкой перед выдачей."""
    code = await get_by_pin(session, pin, for_update=True)
    try:
        if code is None:
            return {"state": ps.BlockState.ALREADY_BLOCKED_OR_NOT_FOUND}
        forced = ps.resolve(code.scenario).block
        if forced is not None:
            return {"state": forced}
        if code.status == ps.STATUS_PAID:
            return {"state": ps.BlockState.ALREADY_PAID}
        if code.status == ps.STATUS_BLOCKED:
            return {"state": ps.BlockState.ALREADY_BLOCKED_OR_NOT_FOUND}
        if is_expired(code):
            return {"state": ps.BlockState.EXPIRED}
        if code.otp_needed:
            otp_error = ps.check_otp(otp)
            if otp_error is not None:
                return {"state": otp_error}

        code.status = ps.STATUS_BLOCKED
        code.blocked_point_id = point_id
        code.blocked_at = utcnow()
        session.add(
            PayoutEvent(
                code_id=code.id,
                event="blocked",
                point_id=point_id,
                balance_after=code.balance,
                note="Заблокирован точкой для выдачи",
            )
        )
        await session.commit()
        return {"state": ps.BlockState.SUCCESS}
    finally:
        # Отпустить FOR UPDATE, если ветка завершилась без commit.
        if session.in_transaction():
            await session.rollback()


async def pay(
    session: AsyncSession, *, pin: str, currency: int, amount: int, point_id: str, client_timestamp: str
) -> dict:
    """/api/pay: финализировать выдачу. amount — сколько фактически выдал
    терминал; невыданный остаток возвращается на код (код снова active)."""
    code = await get_by_pin(session, pin, for_update=True)
    try:
        if code is None or code.currency != currency:
            return {"state": ps.PayState.NOT_FOUND}
        forced = ps.resolve(code.scenario).pay
        if forced is not None:
            return {"state": forced}
        if code.status != ps.STATUS_BLOCKED or code.blocked_point_id != point_id:
            return {"state": ps.PayState.NOT_BLOCKED}
        if amount > code.balance:
            # Терминал не может выдать больше, чем есть на коде — это ошибка клиента.
            return {"state": ps.PayState.INTERNAL_ERROR}

        code.balance -= amount
        code.status = ps.STATUS_PAID if code.balance == 0 else ps.STATUS_ACTIVE
        code.blocked_point_id = None
        code.blocked_at = None
        full = code.balance == 0
        session.add(
            PayoutEvent(
                code_id=code.id,
                event="paid" if full else "partial",
                point_id=point_id,
                amount=amount,
                balance_after=code.balance,
                client_timestamp=client_timestamp,
                note="Выдано полностью"
                if full
                else f"Выдано {amount}, остаток {code.balance} вернулся на код",
            )
        )
        await session.commit()
        return {"state": ps.PayState.SUCCESS, "amount": amount}
    finally:
        if session.in_transaction():
            await session.rollback()


# --- Действия админа ------------------------------------------------------

async def admin_unblock(session: AsyncSession, code: PayoutCode) -> None:
    """Снять зависшую блокировку (точка заблокировала, но так и не вызвала pay)."""
    if code.status != ps.STATUS_BLOCKED:
        return
    session.add(
        PayoutEvent(
            code_id=code.id,
            event="admin_unblock",
            point_id=code.blocked_point_id,
            balance_after=code.balance,
            note="Блокировка снята админом",
        )
    )
    code.status = ps.STATUS_ACTIVE
    code.blocked_point_id = None
    code.blocked_at = None
    await session.commit()


async def admin_expire(session: AsyncSession, code: PayoutCode) -> None:
    """Сделать код истёкшим прямо сейчас (для проверки state 3)."""
    if code.status == ps.STATUS_PAID:
        return
    code.expires_at = utcnow()
    session.add(
        PayoutEvent(code_id=code.id, event="admin_expire", balance_after=code.balance, note="Срок истёк (админ)")
    )
    await session.commit()


async def admin_update(session: AsyncSession, code: PayoutCode, *, scenario: str, otp_needed: bool) -> None:
    if scenario not in ps.SCENARIOS:
        raise ValueError(f"unknown payout scenario: {scenario}")
    if scenario == code.scenario and otp_needed == code.otp_needed:
        return
    code.scenario = scenario
    code.otp_needed = otp_needed
    session.add(
        PayoutEvent(
            code_id=code.id,
            event="admin_update",
            balance_after=code.balance,
            note=f"Сценарий {scenario}, OTP {'нужен' if otp_needed else 'не нужен'}",
        )
    )
    await session.commit()
