"""Общие фикстуры тестов.

Тесты гоняются на отдельном файле SQLite (не на боевом emulator.db). Переменная
DATABASE_URL выставляется ДО импорта app, потому что engine создаётся при импорте.
"""
import os
import pathlib
import tempfile

_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "pe_pytest.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.database import Base, async_session_maker, engine  # noqa: E402
from app.db_init import seed_admin, seed_scenarios  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ScenarioSetting  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Перед каждым тестом: чистая схема + сиды (сценарии, админ)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await seed_scenarios()
    await seed_admin()
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
