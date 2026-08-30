"""Тесты админки: авторизация, защита роутов, страница API-запросов."""


async def test_protected_route_redirects_to_login(client):
    r = await client.get("/admin/payments")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


async def test_login_wrong_password(client):
    r = await client.post("/admin/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401


async def test_login_and_access(client):
    r = await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == 303
    # кука сессии сохраняется в клиенте → защищённый роут доступен
    r2 = await client.get("/admin/payments")
    assert r2.status_code == 200
    assert "Платежи" in r2.text


async def test_requests_page_shows_check(client, agent):
    await agent.post("/check", json={"requisite": "4111111111110002", "amount": 100})
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    r = await client.get("/admin/requests")
    assert r.status_code == 200
    assert "/check" in r.text
    assert "4111111111110002" in r.text  # реквизит из запроса виден в кабинете


async def test_payment_detail_shows_logs(client, agent):
    pid = (
        await agent.post(
            "/pay", json={"requisite": "4111111111110001", "amount": 100, "idempotency_key": "a1"}
        )
    ).json()["payment_id"]
    await agent.get(f"/status/{pid}")
    await client.post("/admin/login", data={"username": "admin", "password": "admin"})
    r = await client.get(f"/admin/payments/{pid}")
    assert r.status_code == 200
    assert "Логи запросов" in r.text
    assert "/pay" in r.text
