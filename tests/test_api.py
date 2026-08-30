"""Интеграционные тесты агентского API: /check, /pay, /status, auth, идемпотентность."""
import uuid


# --- auth ---

async def test_check_requires_auth(client):
    r = await client.post("/check", json={"requisite": "4111111111110001", "amount": 100})
    assert r.status_code == 401


async def test_status_requires_auth(client):
    r = await client.get("/status/whatever")
    assert r.status_code == 401


# --- /check ---

async def test_check_allowed(agent):
    r = await agent.post("/check", json={"requisite": "4111111111110001", "amount": 10000})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "allowed"
    assert body["holder_name"]  # ФИО присутствует
    assert body["currency"] == "RUB"


async def test_check_declined_only_for_instant_decline(agent):
    r2 = await agent.post("/check", json={"requisite": "4111111111110002", "amount": 1})
    assert r2.json()["status"] == "declined"
    # 0004 (отложенный провал) на этапе check считается allowed
    r4 = await agent.post("/check", json={"requisite": "4111111111110004", "amount": 1})
    assert r4.json()["status"] == "allowed"


async def test_check_echoes_currency(agent):
    r = await agent.post(
        "/check", json={"requisite": "4111111111110001", "amount": 1, "currency": "USD"}
    )
    assert r.json()["currency"] == "USD"


async def test_check_always_200_even_for_declined(agent):
    r = await agent.post("/check", json={"requisite": "4111111111110002", "amount": 1})
    assert r.status_code == 200


# --- валидация ---

async def test_amount_must_be_positive(agent):
    r = await agent.post("/check", json={"requisite": "4111111111110001", "amount": 0})
    assert r.status_code == 422


async def test_requisite_needs_digits(agent):
    r = await agent.post("/check", json={"requisite": "ab", "amount": 100})
    assert r.status_code == 422


# --- /pay ---

async def test_pay_returns_accepted(agent):
    r = await agent.post(
        "/pay",
        json={"requisite": "4111111111110001", "amount": 100, "idempotency_key": "test-key"},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "accepted"  # не раскрывает исход


async def test_pay_requires_idempotency_key(agent):
    r = await agent.post("/pay", json={"requisite": "4111111111110001", "amount": 100})
    assert r.status_code == 422


async def test_pay_is_idempotent(agent):
    body = {"requisite": "4111111111110003", "amount": 500, "idempotency_key": str(uuid.uuid4())}
    r1 = await agent.post("/pay", json=body)
    r2 = await agent.post("/pay", json=body)
    assert r1.json()["payment_id"] == r2.json()["payment_id"]


# --- /status ---

async def test_status_404_for_unknown(agent):
    r = await agent.get("/status/does-not-exist")
    assert r.status_code == 404


async def test_instant_success_visible_via_status(agent):
    pid = (
        await agent.post(
            "/pay", json={"requisite": "4111111111110001", "amount": 100, "idempotency_key": "s1"}
        )
    ).json()["payment_id"]
    r = await agent.get(f"/status/{pid}")
    assert r.json()["status"] == "success"


async def test_instant_decline_visible_via_status(agent):
    pid = (
        await agent.post(
            "/pay", json={"requisite": "4111111111110002", "amount": 100, "idempotency_key": "s2"}
        )
    ).json()["payment_id"]
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "failed"


async def test_delayed_starts_pending(agent):
    pid = (
        await agent.post(
            "/pay", json={"requisite": "4111111111110003", "amount": 100, "idempotency_key": "s3"}
        )
    ).json()["payment_id"]
    assert (await agent.get(f"/status/{pid}")).json()["status"] == "pending"
