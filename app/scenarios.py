"""Таблица сценариев эмулятора.

Поведение платежа определяется последними 4 цифрами реквизита. Здесь описан
источник истины: какой суффикс к какому сценарию ведёт. Роуты НЕ хардкодят логику —
они спрашивают у этого модуля.

Терминология:
- initial_status  — статус, который платёж получает сразу при создании.
- final_status    — статус, в который платёж переходит фоновой задачей (для pending).
                    None означает, что переход не нужен (уже финальный) либо
                    платёж навсегда "зависает" (unknown-таймаут).
- default_delay   — стартовая задержка перехода в секундах; попадает в scenario_settings
                    при первом запуске и дальше редактируется через админку.
"""
from dataclasses import dataclass

# Статус-подтверждение приёма платежа (ответ /pay). Не хранится как рабочее
# состояние платежа — это первый шаг истории и то, что отдаёт /pay.
ACCEPTED_STATUS = "accepted"

# Финальные статусы — из них фоновая задача уже не двигает платёж.
FINAL_STATUSES = {"success", "failed", "unknown"}


@dataclass(frozen=True)
class Scenario:
    key: str
    initial_status: str
    final_status: str | None
    default_delay: int
    description: str


# Ключ — суффикс реквизита (последние 4 цифры).
SCENARIOS: dict[str, Scenario] = {
    "0001": Scenario("instant_success", "success", None, 0, "Успех сразу"),
    "0002": Scenario("instant_decline", "failed", None, 0, "Отказ сразу (decline)"),
    "0003": Scenario("delayed_success", "pending", "success", 10, "pending → success через задержку"),
    "0004": Scenario("delayed_failure", "pending", "failed", 10, "pending → failed через задержку"),
    "0005": Scenario("timeout_unknown", "pending", "unknown", 15, "pending → unknown (таймаут провайдера)"),
}

# Сценарий по умолчанию для любого другого суффикса.
DEFAULT_SCENARIO = Scenario("default_success", "success", None, 0, "По умолчанию: успех сразу")


def suffix_of(requisite: str) -> str:
    """Последние 4 цифры реквизита (только цифры, пробелы/дефисы игнорируются)."""
    digits = "".join(ch for ch in requisite if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def resolve(requisite: str) -> Scenario:
    """Подобрать сценарий по реквизиту."""
    return SCENARIOS.get(suffix_of(requisite), DEFAULT_SCENARIO)


def all_configurable() -> dict[str, Scenario]:
    """Сценарии, у которых есть настраиваемая задержка (те, что уходят в pending)."""
    return {suffix: sc for suffix, sc in SCENARIOS.items() if sc.initial_status == "pending"}
