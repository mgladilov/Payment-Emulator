"""Сессионная авторизация админки.

Отдельный механизм от HTTP Basic Auth агентского API — не путать. Логин человека
в браузере проверяется по таблице admin_users (bcrypt), а факт входа хранится в
подписанной сессионной куке (Starlette SessionMiddleware).
"""
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser
from app.security import verify_password

SESSION_KEY = "admin_user"


class NotAuthenticated(Exception):
    """Кидается зависимостью require_admin; обработчик редиректит на логин."""


def require_admin(request: Request) -> str:
    """Зависимость для /admin/*: вернуть имя админа или потребовать логин."""
    user = request.session.get(SESSION_KEY)
    if not user:
        raise NotAuthenticated()
    return user


async def authenticate_admin(
    username: str, password: str, session: AsyncSession
) -> AdminUser | None:
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
