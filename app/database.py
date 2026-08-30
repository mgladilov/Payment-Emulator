"""Async SQLAlchemy engine / session для SQLite."""
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        """WAL + busy_timeout для каждого соединения SQLite.

        WAL: чтения (например, страницы админки) не блокируются записью запросов.
        busy_timeout: писатель ждёт освобождения БД вместо мгновенной ошибки
        "database is locked". synchronous=NORMAL — разумный баланс для WAL.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: отдаёт сессию на время запроса."""
    async with async_session_maker() as session:
        yield session
