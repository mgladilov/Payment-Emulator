"""Админка: управление учётками — свой пароль, админы, агенты API.

Изменения — POST с редиректом обратно (PRG), результат — flash в сессии.
Исключение — операции, после которых надо показать пароль агента: страница
рендерится сразу в ответ на POST. Класть пароль в сессию нельзя — кука
Starlette подписана, но не зашифрована.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import users
from app.admin_auth import current_admin, login_session
from app.database import get_session
from app.models import AdminUser, AgentAccount
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin-users"])

FLASH_KEY = "users_flash"


def _flash(request: Request, text: str, *, error: bool = False) -> None:
    request.session[FLASH_KEY] = {"text": text, "error": error}


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


async def _form(request: Request) -> dict[str, str]:
    return {k: str(v) for k, v in (await request.form()).items()}


# --- Свой аккаунт ---------------------------------------------------------

@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, me: AdminUser = Depends(current_admin)):
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "admin_user": me.username,
            "flash": request.session.pop(FLASH_KEY, None),
            "min_len": users.MIN_PASSWORD_LENGTH,
        },
    )


@router.post("/account/password", include_in_schema=False)
async def account_password(
    request: Request,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    form = await _form(request)
    if form.get("new_password") != form.get("new_password2"):
        _flash(request, "Новые пароли не совпадают", error=True)
        return _back("/admin/account")
    try:
        await users.change_own_password(session, me, form.get("current_password", ""), form.get("new_password", ""))
    except users.UserError as e:
        _flash(request, str(e), error=True)
        return _back("/admin/account")
    login_session(request, me)  # остаться в системе; прочие сессии этого админа отвалятся
    _flash(request, "Пароль изменён. Другие сессии с этим логином завершены.")
    return _back("/admin/account")


# --- Список учёток --------------------------------------------------------

async def _render_users(request: Request, me: AdminUser, session: AsyncSession, flash: dict | None):
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "admin_user": me.username,
            "me_id": me.id,
            "admins": await users.list_admins(session),
            "agents": await users.list_agents(session),
            "flash": flash,
            "min_len": users.MIN_PASSWORD_LENGTH,
        },
    )


def _show_secret(text: str, agent: AgentAccount, password: str) -> dict:
    """Flash, который показывается один раз прямо в ответе на POST (не через сессию)."""
    return {"text": text, "error": False, "secret": {"username": agent.username, "password": password}}


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _render_users(request, me, session, request.session.pop(FLASH_KEY, None))


# --- Админы ---------------------------------------------------------------

async def _get_admin(session: AsyncSession, admin_id: int) -> AdminUser:
    user = await session.get(AdminUser, admin_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin not found")
    return user


@router.post("/users/admins", include_in_schema=False)
async def admin_create(
    request: Request,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    form = await _form(request)
    try:
        user = await users.create_admin(session, form.get("username", ""), form.get("password", ""))
    except users.UserError as e:
        _flash(request, str(e), error=True)
    else:
        _flash(request, f"Админ «{user.username}» создан")
    return _back("/admin/users")


@router.post("/users/admins/{admin_id}/password", include_in_schema=False)
async def admin_reset_password(
    request: Request,
    admin_id: int,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _get_admin(session, admin_id)
    if user.id == me.id:
        return _back("/admin/account")  # свой пароль — только с вводом текущего
    form = await _form(request)
    try:
        await users.set_admin_password(session, user, form.get("password", ""))
    except users.UserError as e:
        _flash(request, str(e), error=True)
    else:
        _flash(request, f"Пароль админа «{user.username}» изменён, его сессии завершены")
    return _back("/admin/users")


@router.post("/users/admins/{admin_id}/delete", include_in_schema=False)
async def admin_delete(
    request: Request,
    admin_id: int,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _get_admin(session, admin_id)
    try:
        await users.delete_admin(session, user, acting_username=me.username)
    except users.UserError as e:
        _flash(request, str(e), error=True)
    else:
        _flash(request, f"Админ «{user.username}» удалён")
    return _back("/admin/users")


# --- Агенты API -----------------------------------------------------------

async def _get_agent(session: AsyncSession, agent_id: int) -> AgentAccount:
    agent = await session.get(AgentAccount, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.post("/users/agents", include_in_schema=False)
async def agent_create(
    request: Request,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    form = await _form(request)
    try:
        agent, password = await users.create_agent(
            session, form.get("username", ""), form.get("password") or None, form.get("description", "")
        )
    except users.UserError as e:
        _flash(request, str(e), error=True)
        return _back("/admin/users")
    return await _render_users(request, me, session, _show_secret(f"Агент «{agent.username}» создан", agent, password))


@router.post("/users/agents/{agent_id}/password", include_in_schema=False)
async def agent_password(
    request: Request,
    agent_id: int,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_agent(session, agent_id)
    form = await _form(request)
    try:
        password = await users.set_agent_password(session, agent, form.get("password") or None)
    except users.UserError as e:
        _flash(request, str(e), error=True)
        return _back("/admin/users")
    return await _render_users(
        request, me, session, _show_secret(f"Пароль агента «{agent.username}» изменён — старый больше не принимается", agent, password)
    )


@router.post("/users/agents/{agent_id}/toggle", include_in_schema=False)
async def agent_toggle(
    request: Request,
    agent_id: int,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_agent(session, agent_id)
    await users.set_agent_active(session, agent, not agent.is_active)
    _flash(request, f"Агент «{agent.username}» {'включён' if agent.is_active else 'отключён'}")
    return _back("/admin/users")


@router.post("/users/agents/{agent_id}/delete", include_in_schema=False)
async def agent_delete(
    request: Request,
    agent_id: int,
    me: AdminUser = Depends(current_admin),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_agent(session, agent_id)
    await users.delete_agent(session, agent)
    _flash(request, f"Агент «{agent.username}» удалён")
    return _back("/admin/users")
