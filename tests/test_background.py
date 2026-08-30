"""Тесты фоновой задачи автоперехода статусов."""
from sqlalchemy import func, select

from app.background import process_pending_once
from app.database import async_session_maker
from app.models import PaymentStatusHistory
from tests.conftest import set_delay


async def _pay(agent, requisite, key):
    r = await agent.post("/pay", headers={"Idempotency-Key": key}, json={"requisite": requisite, "amount": 100})
    return r.json()["payment_id"]


async def test_delayed_success_transitions(agent):
    await set_delay("0003", 0)  # мгновенный дедлайн
    pid = await _pay(agent, "4111111111110003", "b1")
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "pending"
    n = await process_pending_once()
    assert n == 1
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "success"


async def test_delayed_failure_transitions(agent):
    await set_delay("0004", 0)
    pid = await _pay(agent, "4111111111110004", "b2")
    await process_pending_once()
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "failed"


async def test_timeout_goes_unknown_and_stays(agent):
    await set_delay("0005", 0)
    pid = await _pay(agent, "4111111111110005", "b3")
    await process_pending_once()
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "unknown"
    # повторный проход не двигает финальный статус
    assert await process_pending_once() == 0
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "unknown"


async def test_transition_is_atomic_no_duplicates(agent):
    """Два прохода подряд не создают дубль перехода (защита от второго инстанса)."""
    await set_delay("0003", 0)
    pid = await _pay(agent, "4111111111110003", "b4")
    first = await process_pending_once()
    second = await process_pending_once()
    assert (first, second) == (1, 0)
    async with async_session_maker() as s:
        cnt = await s.scalar(
            select(func.count())
            .select_from(PaymentStatusHistory)
            .where(PaymentStatusHistory.payment_id == pid, PaymentStatusHistory.status == "success")
        )
    assert cnt == 1  # ровно одна запись success


async def test_pending_before_deadline_not_transitioned(agent):
    await set_delay("0003", 3600)  # далёкий дедлайн
    pid = await _pay(agent, "4111111111110003", "b5")
    assert await process_pending_once() == 0
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "pending"
