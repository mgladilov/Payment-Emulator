"""Миграции схемы и первичное наполнение (seed) при старте приложения.

Схема управляется Alembic (папка migrations/): при старте выполняется
`alembic upgrade head`, если не выключено AUTO_MIGRATE=false.

Seed идемпотентен: повторный запуск не плодит дубликаты и не затирает
изменённые через админку задержки.
"""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session_maker
from app.models import AdminUser, AgentAccount, ScenarioSetting
from app.scenarios import SCENARIOS
from app.security import hash_password


ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgrade_head() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logger"] = False  # не трогать логирование приложения
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    # env.py сам крутит asyncio.run() — поэтому в отдельном потоке.
    await asyncio.to_thread(_upgrade_head)


async def seed_scenarios() -> None:
    """Записать стартовые задержки сценариев, не трогая уже существующие строки."""
    async with async_session_maker() as session:
        existing = set((await session.scalars(select(ScenarioSetting.suffix))).all())
        for suffix, sc in SCENARIOS.items():
            if suffix not in existing:
                session.add(
                    ScenarioSetting(
                        suffix=suffix,
                        delay_seconds=sc.default_delay,
                        description=sc.description,
                    )
                )
        await session.commit()


async def seed_admin() -> None:
    """Создать первого админа (ADMIN_USERNAME/ADMIN_PASSWORD), только если админов
    нет совсем: сменённый в админке пароль не затирается, удалённый seed-админ
    не воскресает при рестарте."""
    async with async_session_maker() as session:
        if not await session.scalar(select(func.count()).select_from(AdminUser)):
            session.add(
                AdminUser(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                )
            )
            await session.commit()


async def seed_agent() -> None:
    """Создать первую агентскую учётку (API_USERNAME/API_PASSWORD), только если
    агентов нет совсем. Дальше учётки управляются в админке (/admin/users)."""
    async with async_session_maker() as session:
        if not await session.scalar(select(func.count()).select_from(AgentAccount)):
            session.add(
                AgentAccount(
                    username=settings.api_username,
                    password_hash=hash_password(settings.api_password),
                    description="Создан автоматически при первом старте",
                    is_active=True,
                )
            )
            await session.commit()


async def init_db() -> None:
    if settings.auto_migrate:
        await run_migrations()
    await seed_scenarios()
    await seed_admin()
    await seed_agent()
