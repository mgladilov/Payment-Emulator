"""Общие фикстуры тестов.

Тесты гоняются на отдельной базе PostgreSQL (по умолчанию payment_emulator_test,
переопределяется TEST_DATABASE_URL). Переменные окружения выставляются ДО
импорта app, потому что engine создаётся при импорте. NullPool — соединения
asyncpg привязаны к event loop, а у каждого теста свой цикл.
"""
import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://payment_emulator:payment_emulator@localhost:5432/payment_emulator_test",
)
os.environ["DATABASE_NULL_POOL"] = "true"
os.environ["BCRYPT_ROUNDS"] = "4"  # минимальная стоимость: сиды хешируют пароли перед каждым тестом
# Тесты делают DROP SCHEMA — защита от случайного запуска по рабочей базе.
assert "test" in os.environ["DATABASE_URL"].rsplit("/", 1)[-1], "TEST_DATABASE_URL must point to a *test* database"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import Base, async_session_maker, engine  # noqa: E402
from app import api_auth  # noqa: E402
from app.db_init import run_migrations, seed_admin, seed_agent, seed_scenarios  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ScenarioSetting  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_schema():
    """Один раз за прогон: схема с нуля через миграции Alembic (заодно проверяет их)."""
    import asyncio

    async def _reset():
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await run_migrations()

    asyncio.run(_reset())


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Перед каждым тестом: пустые таблицы + сиды (сценарии, админ, агент)."""
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await seed_scenarios()
    await seed_admin()
    await seed_agent()
    api_auth.invalidate_cache()
    yield


@pytest_asyncio.fixture
async def client():
    """HTTP-клиент без авторизации."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def agent():
    """HTTP-клиент с Basic Auth агентского API."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", auth=("agent", "agent-secret")
    ) as c:
        yield c


async def set_delay(suffix: str, seconds: int) -> None:
    """Хелпер: выставить задержку сценария (эмуляция правки в админке)."""
    async with async_session_maker() as session:
        setting = await session.get(ScenarioSetting, suffix)
        setting.delay_seconds = seconds
        await session.commit()


async def login(client, username: str = "admin", password: str = "admin"):
    """Хелпер: войти в админку этим клиентом."""
    return await client.post("/admin/login", data={"username": username, "password": password})
