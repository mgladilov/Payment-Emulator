"""Веб-админка: сессионный вход, список/деталь платежей, настройки задержек.

Роуты /admin/* защищены сессионной авторизацией (require_admin) — это отдельный
механизм от HTTP Basic Auth агентского API.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import api_logs, holder, scenarios
from app.admin_auth import SESSION_KEY, authenticate_admin, require_admin
from app.database import get_session
from app.models import Payment, ScenarioSetting
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])

# Рабочие статусы платежа (без accepted — он только подтверждение/шаг истории).
STATUS_OPTIONS = ["pending", "success", "failed", "unknown"]


# --- Аутентификация -------------------------------------------------------

@router.get("", include_in_schema=False)
async def admin_root():
    return RedirectResponse("/admin/payments", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.session.get(SESSION_KEY):
        return RedirectResponse("/admin/payments", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    user = await authenticate_admin(username, password, session)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверный логин или пароль"}, status_code=401
        )
    request.session[SESSION_KEY] = user.username
    return RedirectResponse("/admin/payments", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout", include_in_schema=False)
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)


# --- Платежи --------------------------------------------------------------

@router.get("/payments", response_class=HTMLResponse)
async def payments_list(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    status: str | None = None,
):
    stmt = select(Payment).order_by(Payment.created_at.desc())
    if status:
        stmt = stmt.where(Payment.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Payment.id.like(like) | Payment.requisite.like(like))
    payments = (await session.scalars(stmt)).all()
    return templates.TemplateResponse(
        request,
        "payments_list.html",
        {
            "admin_user": admin,
            "payments": payments,
            "statuses": STATUS_OPTIONS,
            "q": q,
            "status": status,
        },
    )


async def _load_payment(session: AsyncSession, payment_id: str) -> Payment:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.history))
    )
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
async def payment_detail(
    request: Request,
    payment_id: str,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    payment = await _load_payment(session, payment_id)
    logs = await api_logs.list_for_payment(session, payment_id)
    return templates.TemplateResponse(
        request,
        "payment_detail.html",
        {
            "admin_user": admin,
            "payment": payment,
            "holder_name": holder.holder_name(payment.requisite),
            "is_final": payment.status in scenarios.FINAL_STATUSES,
            "logs": logs,
        },
    )


@router.get("/payments/{payment_id}/status-block", response_class=HTMLResponse)
async def payment_status_block(
    request: Request,
    payment_id: str,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """HTMX-partial: статус + таймлайн; сам себя опрашивает, пока не финал."""
    payment = await _load_payment(session, payment_id)
    return templates.TemplateResponse(
        request,
        "_status_block.html",
        {"payment": payment, "is_final": payment.status in scenarios.FINAL_STATUSES},
    )


@router.get("/payments/{payment_id}/logs-block", response_class=HTMLResponse)
async def payment_logs_block(
    request: Request,
    payment_id: str,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """HTMX-partial: окно логов платежа; опрашивает себя, пока платёж не финален."""
    payment = await _load_payment(session, payment_id)
    logs = await api_logs.list_for_payment(session, payment_id)
    return templates.TemplateResponse(
        request,
        "_logs_block.html",
        {
            "payment": payment,
            "logs": logs,
            "is_final": payment.status in scenarios.FINAL_STATUSES,
        },
    )


# --- Общий журнал API-запросов -------------------------------------------

@router.get("/requests", response_class=HTMLResponse)
async def api_requests(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    endpoint: str | None = None,
    q: str | None = None,
):
    logs = await api_logs.list_all(session, endpoint=endpoint, q=q)
    return templates.TemplateResponse(
        request,
        "requests.html",
        {
            "admin_user": admin,
            "logs": logs,
            "endpoints": ["check", "pay", "status"],
            "endpoint": endpoint,
            "q": q,
        },
    )


# --- Настройки задержек ---------------------------------------------------

async def _settings_rows(session: AsyncSession) -> list[dict]:
    rows = []
    for suffix, sc in sorted(scenarios.all_configurable().items()):
        setting = await session.get(ScenarioSetting, suffix)
        rows.append(
            {
                "suffix": suffix,
                "description": sc.description,
                "final_status": sc.final_status,
                "delay_seconds": setting.delay_seconds if setting else sc.default_delay,
            }
        )
    return rows


@router.get("/settings", response_class=HTMLResponse)
async def settings_form(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    saved: int = 0,
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"admin_user": admin, "rows": await _settings_rows(session), "saved": bool(saved)},
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    for suffix in scenarios.all_configurable():
        raw = form.get(f"delay_{suffix}")
        if raw is None:
            continue
        try:
            delay = max(0, min(3600, int(raw)))
        except (TypeError, ValueError):
            continue
        setting = await session.get(ScenarioSetting, suffix)
        if setting is not None:
            setting.delay_seconds = delay
    await session.commit()
    return RedirectResponse("/admin/settings?saved=1", status_code=status.HTTP_303_SEE_OTHER)
