"""Справочник эмулятора выдачи наличных: коды state, сценарии, OTP-заглушки.

Источник истины для API выдач (/api/v2/check, /api/block, /api/pay) — роуты и
сервис НЕ хардкодят числа, а берут их отсюда.

Большинство ответов возникает естественно из состояния кода (не найден, уже
выплачен, истёк, уже заблокирован, не заблокирован). Ответы, которые из
состояния не получить (антифрод, лимиты, истёкший сценарий, внутренняя ошибка),
задаются принудительным сценарием кода — он выбирается при генерации и
меняется в админке на лету.
"""
from dataclasses import dataclass
from enum import IntEnum


class CheckState(IntEnum):
    SUCCESS = 0
    NOT_FOUND = 1
    ALREADY_PAID = 2
    EXPIRED = 3
    SCENARIO_BLOCKED = 9
    LIMITED = 10
    INTERNAL_ERROR = -100


class BlockState(IntEnum):
    SUCCESS = 0
    ALREADY_BLOCKED_OR_NOT_FOUND = 1
    ALREADY_PAID = 2
    EXPIRED = 3
    SCENARIO_EXPIRED = 8
    SCENARIO_BLOCKED = 9
    OTP_MISSING = 11
    OTP_EXPIRED = 12
    OTP_INCORRECT = 13
    INTERNAL_ERROR = -100


class PayState(IntEnum):
    SUCCESS = 0
    NOT_FOUND = 1
    NOT_BLOCKED = 4
    INTERNAL_ERROR = -100


# Рабочие статусы кода (хранятся в БД) + вычисляемый "expired".
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
STATUS_PAID = "paid"
STATUS_EXPIRED = "expired"  # не хранится: active + expires_at в прошлом
STATUS_OPTIONS = [STATUS_ACTIVE, STATUS_BLOCKED, STATUS_PAID, STATUS_EXPIRED]


@dataclass(frozen=True)
class PayoutScenario:
    key: str
    description: str
    check: CheckState | None = None  # принудительный ответ /api/v2/check (None — по состоянию)
    block: BlockState | None = None  # принудительный ответ /api/block
    pay: PayState | None = None      # принудительный ответ /api/pay


# Ключ — значение поля scenario у кода выдачи.
SCENARIOS: dict[str, PayoutScenario] = {
    sc.key: sc
    for sc in (
        PayoutScenario("normal", "Обычный: ответы по состоянию кода"),
        PayoutScenario(
            "scenario_blocked",
            "Сценарий заблокирован: check и block → 9",
            check=CheckState.SCENARIO_BLOCKED,
            block=BlockState.SCENARIO_BLOCKED,
        ),
        PayoutScenario("limited", "Лимит: check → 10 (block/pay по состоянию)", check=CheckState.LIMITED),
        PayoutScenario(
            "scenario_expired", "Сценарий истёк: block → 8 (check проходит)", block=BlockState.SCENARIO_EXPIRED
        ),
        PayoutScenario(
            "internal_error",
            "Внутренняя ошибка: check, block и pay → -100",
            check=CheckState.INTERNAL_ERROR,
            block=BlockState.INTERNAL_ERROR,
            pay=PayState.INTERNAL_ERROR,
        ),
    )
}
DEFAULT_SCENARIO = "normal"


def resolve(key: str) -> PayoutScenario:
    return SCENARIOS.get(key, SCENARIOS[DEFAULT_SCENARIO])


# --- OTP-заглушка ---------------------------------------------------------
# Если у кода otpNeeded=true, /api/block проверяет поле otp:
#   нет / пустой → 11, "0000" → успех, "0002" → 12 (истёк), любой другой → 13.
OTP_VALID = "0000"
OTP_EXPIRED = "0002"


def check_otp(otp: str | None) -> BlockState | None:
    """None — OTP верный; иначе код ошибки для /api/block."""
    if not otp:
        return BlockState.OTP_MISSING
    if otp == OTP_VALID:
        return None
    if otp == OTP_EXPIRED:
        return BlockState.OTP_EXPIRED
    return BlockState.OTP_INCORRECT
