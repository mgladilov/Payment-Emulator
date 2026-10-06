"""Тесты API выдачи наличных: /api/v2/check, /api/block, /api/pay + админка выдач."""
import asyncio
from datetime import timedelta

from sqlalchemy import select

from app import payouts
from app.database import async_session_maker
from app.models import PayoutCode, utcnow

CUR = 10
POINT = "123"


async def _code(amount=1000, **kw) -> str:
    async with async_session_maker() as s:
        params = {"count": 1, "amount": amount, "currency": CUR, "ttl_hours": 24, **kw}
        return (await payouts.create_codes(s, **params))[0].pin


async def _check(agent, pin, currency=CUR):
    return (await agent.post("/api/v2/check", json={"pin": pin, "currency": currency, "pointId": POINT})).json()


async def _block(agent, pin, otp="0000", point=POINT):
    return (await agent.post("/api/block", json={"pin": pin, "pointId": point, "otp": otp})).json()


async def _pay(agent, pin, amount, point=POINT, currency=CUR):
    body = {"timestamp": "2022-12-12T00:00:00", "pin": pin, "currency": currency, "amount": amount, "pointId": point}
    return (await agent.post("/api/pay", json=body)).json()


async def _get(pin) -> PayoutCode:
    async with async_session_maker() as s:
        return await s.scalar(select(PayoutCode).where(PayoutCode.pin == pin))


# --- генерация ---

def test_generated_pin_format():
    for _ in range(200):
        pin = payouts.generate_pin()
        assert len(pin) == 12 and pin.isdigit() and pin[0] != "0"


async def test_payout_api_requires_auth(client):
    r = await client.post("/api/v2/check", json={"pin": "1", "currency": CUR, "pointId": POINT})
    assert r.status_code == 401


# --- check ---

async def test_check_success_full_body(agent):
    pin = await _code(500, phone="996000111222", fio="FirstName LastName", exchange_id=1)
    body = await _check(agent, pin)
    assert body == {
        "state": 0,
        "amount": 500,
        "phone": "996000111222",
        "fio": "FirstName LastName",
        "exchangeId": 1,
        "otpNeeded": True,
        "rate": 1,
        "commission": 0,
    }


async def test_check_nullable_fields_are_null(agent):
    body = await _check(agent, await _code(otp_needed=False))
    assert body["phone"] is None and body["fio"] is None and body["exchangeId"] is None
    assert body["otpNeeded"] is False


async def test_check_not_found_and_currency_mismatch(agent):
    assert await _check(agent, "999999999999") == {"state": 1}
    assert await _check(agent, "abc") == {"state": 1}
    assert await _check(agent, await _code(), currency=840) == {"state": 1}


async def test_check_accepts_numeric_pin(agent):
    pin = await _code()
    r = await agent.post("/api/v2/check", json={"pin": int(pin), "currency": CUR, "pointId": 123})
    assert r.json()["state"] == 0


async def test_check_expired(agent):
    pin = await _code()
    async with async_session_maker() as s:
        code = await s.scalar(select(PayoutCode).where(PayoutCode.pin == pin))
        code.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    assert await _check(agent, pin) == {"state": 3}


async def test_check_forced_scenarios(agent):
    assert await _check(agent, await _code(scenario="scenario_blocked")) == {"state": 9}
    assert await _check(agent, await _code(scenario="limited")) == {"state": 10}
    assert await _check(agent, await _code(scenario="internal_error")) == {"state": -100}
    assert (await _check(agent, await _code(scenario="scenario_expired")))["state"] == 0


async def test_invalid_body_is_state_minus_100(agent):
    r = await agent.post("/api/v2/check", json={"pin": "123456789012"})
    assert r.status_code == 200
    assert r.json() == {"state": -100}


# --- block ---

async def test_block_otp_stub(agent):
    pin = await _code()
    assert await _block(agent, pin, otp=None) == {"state": 11}
    assert await _block(agent, pin, otp="") == {"state": 11}
    assert await _block(agent, pin, otp="0002") == {"state": 12}
    assert await _block(agent, pin, otp="0001") == {"state": 13}
    assert await _block(agent, pin, otp="0000") == {"state": 0}


async def test_block_without_otp_when_not_needed(agent):
    assert await _block(agent, await _code(otp_needed=False), otp=None) == {"state": 0}


async def test_block_twice_is_already_blocked(agent):
    pin = await _code()
    assert await _block(agent, pin) == {"state": 0}
    assert await _block(agent, pin) == {"state": 1}
    assert await _block(agent, pin, point="other") == {"state": 1}


async def test_block_not_found_expired_and_forced(agent):
    assert await _block(agent, "999999999999") == {"state": 1}
    assert await _block(agent, await _code(scenario="scenario_expired")) == {"state": 8}
    assert await _block(agent, await _code(scenario="scenario_blocked")) == {"state": 9}
    pin = await _code()
    async with async_session_maker() as s:
        await payouts.admin_expire(s, await s.scalar(select(PayoutCode).where(PayoutCode.pin == pin)))
    assert await _block(agent, pin) == {"state": 3}


async def test_concurrent_block_only_one_wins(agent):
    pin = await _code()
    results = await asyncio.gather(*[_block(agent, pin, point=str(i)) for i in range(10)])
    assert sorted(r["state"] for r in results) == [0] + [1] * 9


# --- pay ---

async def test_full_flow_check_block_pay(agent):
    pin = await _code(1000)
    assert (await _check(agent, pin))["amount"] == 1000
    assert await _block(agent, pin) == {"state": 0}
    assert await _pay(agent, pin, 1000) == {"state": 0, "amount": 1000}
    assert await _check(agent, pin) == {"state": 2}
    assert await _block(agent, pin) == {"state": 2}
    assert (await _get(pin)).status == "paid"


async def test_partial_payout_returns_remainder(agent):
    pin = await _code(1000)
    await _block(agent, pin)
    assert await _pay(agent, pin, 400) == {"state": 0, "amount": 400}
    code = await _get(pin)
    assert (code.status, code.balance) == ("active", 600)
    assert (await _check(agent, pin))["amount"] == 600
    # остаток можно снять повторным циклом block → pay
    await _block(agent, pin)
    assert await _pay(agent, pin, 600) == {"state": 0, "amount": 600}
    assert (await _get(pin)).status == "paid"


async def test_pay_zero_releases_block(agent):
    pin = await _code(1000)
    await _block(agent, pin)
    assert await _pay(agent, pin, 0) == {"state": 0, "amount": 0}
    code = await _get(pin)
    assert (code.status, code.balance) == ("active", 1000)


async def test_pay_not_blocked(agent):
    pin = await _code()
    assert await _pay(agent, pin, 100) == {"state": 4}
    await _block(agent, pin, point="A")
    assert await _pay(agent, pin, 100, point="B") == {"state": 4}  # заблокирован другой точкой
    assert await _pay(agent, pin, 100, point="A") == {"state": 0, "amount": 100}
    assert await _pay(agent, pin, 100, point="A") == {"state": 4}  # повтор — блок уже снят


async def test_pay_not_found(agent):
    assert await _pay(agent, "999999999999", 100) == {"state": 1}
    pin = await _code()
    await _block(agent, pin)
    assert await _pay(agent, pin, 100, currency=840) == {"state": 1}


async def test_pay_more_than_balance_is_error(agent):
    pin = await _code(500)
    await _block(agent, pin)
    assert await _pay(agent, pin, 501) == {"state": -100}
    assert (await _get(pin)).status == "blocked"  # состояние не тронуто


async def test_pay_forced_internal_error(agent):
    pin = await _code(otp_needed=False)
    async with async_session_maker() as s:
        code = await s.scalar(select(PayoutCode).where(PayoutCode.pin == pin))
        await payouts.admin_update(s, code, scenario="internal_error", otp_needed=False)
    assert await _pay(agent, pin, 100) == {"state": -100}


async def test_blocked_code_does_not_expire_for_pay(agent):
    pin = await _code(1000)
    await _block(agent, pin)
    async with async_session_maker() as s:
        code = await s.scalar(select(PayoutCode).where(PayoutCode.pin == pin))
        code.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    assert await _pay(agent, pin, 1000) == {"state": 0, "amount": 1000}


# --- журнал и админка ---

async def test_payout_calls_are_logged(client, agent):
    pin = await _code()
    await _check(agent, pin)
    await _block(agent, pin, otp="0001")
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    r = await client.get("/admin/requests", params={"endpoint": "payout_block"})
    assert pin in r.text and "/api/block" in r.text
    detail = await client.get(f"/admin/payouts/{(await _get(pin)).id}")
    assert "/api/v2/check" in detail.text and "/api/block" in detail.text


async def test_admin_generate_and_list(client):
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    r = await client.post(
        "/admin/payouts/generate",
        data={"amount": "750", "currency": "10", "count": "3", "ttl_hours": "24", "scenario": "normal",
              "otp_needed": "on", "rate": "87,4", "commission": "5"},
    )
    assert r.status_code == 303
    page = await client.get("/admin/payouts")
    async with async_session_maker() as s:
        codes = (await s.scalars(select(PayoutCode))).all()
    assert len(codes) == 3
    assert all(c.amount == 750 and c.balance == 750 and float(c.rate) == 87.4 for c in codes)
    assert all(c.pin in page.text for c in codes)
    assert "Сгенерировано кодов: 3" in page.text


async def test_admin_unblock(client, agent):
    pin = await _code()
    await _block(agent, pin)
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    code = await _get(pin)
    await client.post(f"/admin/payouts/{code.id}/unblock")
    assert (await _get(pin)).status == "active"
    assert await _block(agent, pin) == {"state": 0}


async def test_admin_status_filter_expired(client):
    live = await _code()
    dead = await _code()
    async with async_session_maker() as s:
        await payouts.admin_expire(s, await s.scalar(select(PayoutCode).where(PayoutCode.pin == dead)))
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    expired = (await client.get("/admin/payouts", params={"status": "expired"})).text
    active = (await client.get("/admin/payouts", params={"status": "active"})).text
    assert dead in expired and live not in expired
    assert live in active and dead not in active
