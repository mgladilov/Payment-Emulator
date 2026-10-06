"""HTTP Basic Auth для агентского API (/check, /pay, /status, /api/*).

Учётки агентов лежат в таблице agent_accounts и управляются через админку
(/admin/users). Это отдельный механизм от сессионной авторизации админки.

bcrypt намеренно медленный (~0.2с), а Basic Auth проверяется на каждом
запросе — поэтому успешная проверка кэшируется в памяти процесса на
CACHE_TTL секунд (ключ — логин + SHA-256 пароля, сам пароль не хранится).
Любое изменение агентов в админке сбрасывает кэш (invalidate_cache), так что
отключение/смена пароля действуют сразу. При нескольких воркерах uvicorn
остальные процессы увидят изменение не позже чем через CACHE_TTL.
"""
import asyncio
import hashlib
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import AgentAccount
from app.security import hash_password, verify_password

CACHE_TTL = 30 * 60.0  # 30 минут: эмулятор тестовый, изменения в админке всё равно сбрасывают кэш

_basic = HTTPBasic()
_cache: dict[tuple[str, str], float] = {}  # (логин, sha256(пароль)) → момент истечения (monotonic)
# Хеш-пустышка: для несуществующего логина тоже гоняем bcrypt, чтобы по
# времени ответа нельзя было отличить «нет логина» от «неверный пароль».
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def invalidate_cache() -> None:
    _cache.clear()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


async def require_api_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(_basic),
    session: AsyncSession = Depends(get_session),
) -> str:
    key = (credentials.username, hashlib.sha256(credentials.password.encode("utf-8")).hexdigest())
    now = time.monotonic()
    if _cache.get(key, 0.0) < now:
        agent = await session.scalar(
            select(AgentAccount).where(AgentAccount.username == credentials.username)
        )
        pw_hash = agent.password_hash if agent is not None else _DUMMY_HASH
        ok = await asyncio.to_thread(verify_password, credentials.password, pw_hash)
        if not (ok and agent is not None and agent.is_active):
            raise _unauthorized()
        _cache[key] = now + CACHE_TTL
    # Логин агента нужен журналу API-запросов (api_request_log.agent).
    request.state.api_user = credentials.username
    return credentials.username


def current_agent(request: Request) -> str | None:
    return getattr(request.state, "api_user", None)
