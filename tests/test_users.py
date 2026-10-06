"""Тесты управления учётками: свой пароль, админы, агенты API."""
import re

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.database import async_session_maker
from app.db_init import seed_admin, seed_agent
from app.main import app
from app.models import AdminUser, AgentAccount
from tests.conftest import login

CHECK = {"requisite": "4111111111110001", "amount": 100}


def _new_client(auth=None) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", auth=auth)


async def _id(model, username) -> int:
    async with async_session_maker() as s:
        return await s.scalar(select(model.id).where(model.username == username))


def _shown_password(html: str, username: str) -> str:
    m = re.search(rf"{re.escape(username)}:(\S+?)</div>", html)
    assert m, "пароль не показан"
    return m.group(1)


# --- seed ---

async def test_seed_only_when_empty():
    async with async_session_maker() as s:
        admin = await s.scalar(select(AdminUser))
        admin.username = "renamed"
        await s.commit()
    await seed_admin()
    await seed_agent()
    async with async_session_maker() as s:
        assert await s.scalar(select(func.count()).select_from(AdminUser)) == 1  # admin не воскрес
        assert await s.scalar(select(func.count()).select_from(AgentAccount)) == 1


# --- свой пароль ---

async def test_change_own_password(client):
    await login(client)
    r = await client.post(
        "/admin/account/password",
        data={"current_password": "admin", "new_password": "new-secret-1", "new_password2": "new-secret-1"},
    )
    assert r.status_code == 303
    assert (await client.get("/admin/payments")).status_code == 200  # текущая сессия жива
    async with _new_client() as other:
        assert (await login(other, "admin", "admin")).status_code == 401
        assert (await login(other, "admin", "new-secret-1")).status_code == 303


async def test_change_own_password_wrong_current(client):
    await login(client)
    await client.post(
        "/admin/account/password",
        data={"current_password": "nope", "new_password": "new-secret-1", "new_password2": "new-secret-1"},
    )
    assert "Текущий пароль указан неверно" in (await client.get("/admin/account")).text
    async with _new_client() as other:
        assert (await login(other)).status_code == 303  # пароль не поменялся


async def test_short_password_rejected(client):
    await login(client)
    await client.post(
        "/admin/account/password",
        data={"current_password": "admin", "new_password": "short", "new_password2": "short"},
    )
    assert "минимум" in (await client.get("/admin/account")).text


async def test_password_change_kills_other_sessions(client):
    await login(client)
    async with _new_client() as second:
        await login(second)
        assert (await second.get("/admin/payments")).status_code == 200
        await client.post(
            "/admin/account/password",
            data={"current_password": "admin", "new_password": "new-secret-1", "new_password2": "new-secret-1"},
        )
        r = await second.get("/admin/payments")
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"


# --- админы ---

async def test_create_admin_and_login(client):
    await login(client)
    await client.post("/admin/users/admins", data={"username": "ops", "password": "ops-password"})
    async with _new_client() as other:
        assert (await login(other, "ops", "ops-password")).status_code == 303


async def test_duplicate_admin_rejected(client):
    await login(client)
    await client.post("/admin/users/admins", data={"username": "admin", "password": "whatever-1"})
    assert "уже существует" in (await client.get("/admin/users")).text


async def test_cannot_delete_self_or_last_admin(client):
    await login(client)
    await client.post(f"/admin/users/admins/{await _id(AdminUser, 'admin')}/delete")
    assert "Нельзя удалить самого себя" in (await client.get("/admin/users")).text
    assert await _id(AdminUser, "admin") is not None


async def test_deleted_admin_session_dies(client):
    await login(client)
    await client.post("/admin/users/admins", data={"username": "ops", "password": "ops-password"})
    async with _new_client() as ops:
        await login(ops, "ops", "ops-password")
        await client.post(f"/admin/users/admins/{await _id(AdminUser, 'ops')}/delete")
        assert (await ops.get("/admin/payments")).status_code == 303


async def test_reset_other_admin_password(client):
    await login(client)
    await client.post("/admin/users/admins", data={"username": "ops", "password": "ops-password"})
    await client.post(f"/admin/users/admins/{await _id(AdminUser, 'ops')}/password", data={"password": "reset-pass-1"})
    async with _new_client() as ops:
        assert (await login(ops, "ops", "reset-pass-1")).status_code == 303


# --- агенты ---

async def test_seed_agent_works(agent):
    assert (await agent.post("/check", json=CHECK)).status_code == 200


async def test_create_agent_with_generated_password(client):
    await login(client)
    r = await client.post("/admin/users/agents", data={"username": "bot1", "password": "", "description": "test"})
    assert r.status_code == 200
    password = _shown_password(r.text, "bot1")
    assert len(password) >= 20
    # пароль не задерживается в сессии: на следующей странице его нет
    assert password not in (await client.get("/admin/users")).text
    async with _new_client(auth=("bot1", password)) as bot:
        assert (await bot.post("/check", json=CHECK)).status_code == 200


async def test_disable_agent_takes_effect_immediately(client, agent):
    assert (await agent.post("/check", json=CHECK)).status_code == 200  # прогреть кэш
    await login(client)
    await client.post(f"/admin/users/agents/{await _id(AgentAccount, 'agent')}/toggle")
    assert (await agent.post("/check", json=CHECK)).status_code == 401
    await client.post(f"/admin/users/agents/{await _id(AgentAccount, 'agent')}/toggle")
    assert (await agent.post("/check", json=CHECK)).status_code == 200


async def test_agent_password_change_revokes_old(client, agent):
    assert (await agent.post("/check", json=CHECK)).status_code == 200
    await login(client)
    r = await client.post(f"/admin/users/agents/{await _id(AgentAccount, 'agent')}/password", data={"password": "brand-new-pass"})
    assert _shown_password(r.text, "agent") == "brand-new-pass"
    assert (await agent.post("/check", json=CHECK)).status_code == 401
    async with _new_client(auth=("agent", "brand-new-pass")) as fresh:
        assert (await fresh.post("/check", json=CHECK)).status_code == 200


async def test_delete_agent(client, agent):
    await login(client)
    await client.post(f"/admin/users/agents/{await _id(AgentAccount, 'agent')}/delete")
    assert (await agent.post("/check", json=CHECK)).status_code == 401


async def test_unknown_agent_rejected(client):
    async with _new_client(auth=("ghost", "whatever")) as ghost:
        assert (await ghost.post("/check", json=CHECK)).status_code == 401


async def test_request_log_records_agent(client, agent):
    await agent.post("/check", json=CHECK)
    await login(client)
    r = await client.get("/admin/requests", params={"q": "agent"})
    assert "agent=agent" in r.text


async def test_users_page_requires_login(client):
    r = await client.get("/admin/users")
    assert r.status_code == 303
