"""Учётки: админы веб-админки и агенты API (HTTP Basic Auth).

Пароли хранятся только в виде bcrypt-хеша. Проверки, которые могут снять
доступ (удаление/смена пароля админа, последний админ), живут здесь, а не в
роутах — роуты лишь показывают результат.
"""
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import api_auth
from app.models import AdminUser, AgentAccount
from app.security import hash_password, verify_password

MIN_PASSWORD_LENGTH = 8
USERNAME_MAX = 64


class UserError(ValueError):
    """Ошибка, которую можно показать админу как есть."""


def generate_password() -> str:
    return secrets.token_urlsafe(18)  # 24 символа


def _check_username(username: str) -> str:
    username = username.strip()
    if not username or len(username) > USERNAME_MAX:
        raise UserError(f"Логин: от 1 до {USERNAME_MAX} символов")
    if ":" in username:
        raise UserError("Логин не может содержать ':' (ломает HTTP Basic Auth)")
    return username


def _check_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Пароль: минимум {MIN_PASSWORD_LENGTH} символов")
    return password


# --- Админы ---------------------------------------------------------------

async def list_admins(session: AsyncSession) -> list[AdminUser]:
    return list((await session.scalars(select(AdminUser).order_by(AdminUser.id))).all())


async def create_admin(session: AsyncSession, username: str, password: str) -> AdminUser:
    username = _check_username(username)
    _check_password(password)
    if await session.scalar(select(AdminUser).where(AdminUser.username == username)):
        raise UserError(f"Админ «{username}» уже существует")
    user = AdminUser(username=username, password_hash=hash_password(password))
    session.add(user)
    await session.commit()
    return user


async def set_admin_password(session: AsyncSession, user: AdminUser, password: str) -> None:
    """Сменить пароль. Все сессии этого админа (кроме обновлённой вызывающим) станут невалидны."""
    user.password_hash = hash_password(_check_password(password))
    await session.commit()


async def change_own_password(session: AsyncSession, user: AdminUser, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise UserError("Текущий пароль указан неверно")
    await set_admin_password(session, user, new)


async def delete_admin(session: AsyncSession, user: AdminUser, *, acting_username: str) -> None:
    if user.username == acting_username:
        raise UserError("Нельзя удалить самого себя")
    if await session.scalar(select(func.count()).select_from(AdminUser)) <= 1:
        raise UserError("Нельзя удалить последнего админа")
    await session.delete(user)
    await session.commit()


# --- Агенты API -----------------------------------------------------------

async def list_agents(session: AsyncSession) -> list[AgentAccount]:
    return list((await session.scalars(select(AgentAccount).order_by(AgentAccount.id))).all())


async def create_agent(
    session: AsyncSession, username: str, password: str | None, description: str = ""
) -> tuple[AgentAccount, str]:
    """Создать агента. Пустой пароль → сгенерировать. Возвращает (агент, пароль)."""
    username = _check_username(username)
    password = _check_password(password) if password else generate_password()
    if await session.scalar(select(AgentAccount).where(AgentAccount.username == username)):
        raise UserError(f"Агент «{username}» уже существует")
    agent = AgentAccount(
        username=username,
        password_hash=hash_password(password),
        description=description.strip()[:255],
        is_active=True,
    )
    session.add(agent)
    await session.commit()
    api_auth.invalidate_cache()
    return agent, password


async def set_agent_password(session: AsyncSession, agent: AgentAccount, password: str | None) -> str:
    """Сменить пароль агента (пустой → сгенерировать). Возвращает новый пароль."""
    password = _check_password(password) if password else generate_password()
    agent.password_hash = hash_password(password)
    await session.commit()
    api_auth.invalidate_cache()
    return password


async def set_agent_active(session: AsyncSession, agent: AgentAccount, active: bool) -> None:
    agent.is_active = active
    await session.commit()
    api_auth.invalidate_cache()


async def delete_agent(session: AsyncSession, agent: AgentAccount) -> None:
    await session.delete(agent)
    await session.commit()
    api_auth.invalidate_cache()
