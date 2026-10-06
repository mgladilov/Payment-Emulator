"""Async SQLAlchemy engine / session для PostgreSQL (asyncpg)."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if settings.database_null_pool:
    _engine_kwargs = {"echo": False, "poolclass": NullPool}

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: отдаёт сессию на время запроса."""
    async with async_session_maker() as session:
        yield session
