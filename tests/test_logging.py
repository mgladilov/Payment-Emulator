"""Тесты логирования API-запросов в БД (api_request_log)."""
from sqlalchemy import select

from app.database import async_session_maker
from app.models import ApiRequestLog


async def _logs(endpoint=None):
    async with async_session_maker() as s:
        stmt = select(ApiRequestLog).order_by(ApiRequestLog.id)
        if endpoint:
            stmt = stmt.where(ApiRequestLog.endpoint == endpoint)
        return list((await s.scalars(stmt)).all())


async def test_check_is_logged_without_payment(agent):
    await agent.post("/check", json={"requisite": "4111111111110002", "amount": 500, "currency": "USD"})
    rows = await _logs("check")
    assert len(rows) == 1
    row = rows[0]
    assert row.payment_id is None  # check не создаёт платёж
    assert "4111111111110002" in row.request_body
    assert "declined" in row.response_body


async def test_pay_and_status_logged_with_payment_id(agent):
    pid = (
        await agent.post(
            "/pay", headers={"Idempotency-Key": "l1"}, json={"requisite": "4111111111110001", "amount": 100}
        )
    ).json()["payment_id"]
    await agent.get(f"/status/{pid}")

    pay_rows = await _logs("pay")
    status_rows = await _logs("status")
    assert pay_rows[0].payment_id == pid
    assert "Idempotency-Key" in pay_rows[0].request_body  # заголовок попал в лог запроса
    assert status_rows[0].payment_id == pid
    assert "success" in status_rows[0].response_body


async def test_logging_does_not_break_request_even_if_many(agent):
    # серия запросов не должна падать и должна накопить логи
    for i in range(5):
        await agent.post("/check", json={"requisite": f"41111111111100{i:02d}", "amount": 100})
    assert len(await _logs("check")) == 5
