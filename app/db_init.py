"""Создание таблиц и первичное наполнение (seed) при старте приложения.

Seed идемпотентен: повторный запуск не плодит дубликаты и не затирает
изменённые через админку задержки.
"""
from sqlalchemy import select

from app.config import settings
from app.database import Base, async_session_maker, engine
from app.models import AdminUser, ScenarioSetting
from app.scenarios import SCENARIOS
from app.security import hash_password


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
    """Создать seed-админа, если его ещё нет."""
    async with async_session_maker() as session:
        exists = await session.scalar(
            select(AdminUser).where(AdminUser.username == settings.admin_username)
        )
        if exists is None:
            session.add(
                AdminUser(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                )
            )
            await session.commit()


async def init_db() -> None:
    await create_tables()
    await seed_scenarios()
    await seed_admin()
