"""Сессионная авторизация админки.

Отдельный механизм от HTTP Basic Auth агентского API — не путать. Логин человека
в браузере проверяется по таблице admin_users (bcrypt), а факт входа хранится в
подписанной сессионной куке (Starlette SessionMiddleware).

В куке рядом с логином лежит «штамп пароля» — короткий HMAC от текущего
bcrypt-хеша. На каждом запросе админ перечитывается из БД: если его удалили
или сменили пароль, штамп не совпадёт и сессия станет невалидной.
"""
import hashlib
import hmac

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import AdminUser
from app.security import verify_password

SESSION_KEY = "admin_user"
SESSION_STAMP_KEY = "admin_pw_stamp"


class NotAuthenticated(Exception):
    """Кидается зависимостью require_admin; обработчик редиректит на логин."""


def password_stamp(user: AdminUser) -> str:
    """Штамп пароля для куки. HMAC, а не кусок хеша: кука подписана, но не
    зашифрована, светить в ней даже часть bcrypt-хеша незачем."""
    return hmac.new(
        settings.session_secret.encode(), user.password_hash.encode(), hashlib.sha256
    ).hexdigest()[:16]


def login_session(request: Request, user: AdminUser) -> None:
    """Записать (или обновить после смены пароля) вход в сессию."""
    request.session[SESSION_KEY] = user.username
    request.session[SESSION_STAMP_KEY] = password_stamp(user)


async def current_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> AdminUser:
    """Зависимость: текущий админ из БД или требование залогиниться."""
    username = request.session.get(SESSION_KEY)
    if not username:
        raise NotAuthenticated()
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not hmac.compare_digest(
        request.session.get(SESSION_STAMP_KEY, ""), password_stamp(user)
    ):
        request.session.clear()
        raise NotAuthenticated()
    return user


async def require_admin(user: AdminUser = Depends(current_admin)) -> str:
    """Зависимость для /admin/*: имя текущего админа."""
    return user.username


async def authenticate_admin(
    username: str, password: str, session: AsyncSession
) -> AdminUser | None:
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
