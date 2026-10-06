"""Веб-админка: сессионный вход, список/деталь платежей, коды выдачи, настройки задержек.

Роуты /admin/* защищены сессионной авторизацией (require_admin) — это отдельный
механизм от HTTP Basic Auth агентского API.
"""
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import api_logs, holder, payout_scenarios, payouts, scenarios
from app.admin_auth import SESSION_KEY, authenticate_admin, login_session, require_admin
from app.database import get_session
from app.models import Payment, PayoutCode, ScenarioSetting, utcnow
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])

# Рабочие статусы платежа (без accepted — он только подтверждение/шаг истории).
STATUS_OPTIONS = ["pending", "success", "failed", "unknown"]

# Фильтр журнала API-запросов: эндпоинты платежей и выдач.
API_LOG_ENDPOINTS = ["check", "pay", "status", "payout_check", "payout_block", "payout_pay"]

# Сессионный ключ для одноразового показа только что сгенерированных PIN.
GENERATED_PINS_KEY = "generated_pins"


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
    login_session(request, user)
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
            "endpoints": API_LOG_ENDPOINTS,
            "endpoint": endpoint,
            "q": q,
        },
    )


# --- Коды выдачи -----------------------------------------------------------

@router.get("/payouts", response_class=HTMLResponse)
async def payouts_list(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    status: str | None = None,
):
    now = utcnow()
    stmt = select(PayoutCode).order_by(PayoutCode.created_at.desc(), PayoutCode.id.desc())
    # "expired" не хранится — это active с истёкшим сроком; active — только живые.
    if status == payout_scenarios.STATUS_EXPIRED:
        stmt = stmt.where(PayoutCode.status == payout_scenarios.STATUS_ACTIVE, PayoutCode.expires_at <= now)
    elif status == payout_scenarios.STATUS_ACTIVE:
        stmt = stmt.where(PayoutCode.status == status, PayoutCode.expires_at > now)
    elif status:
        stmt = stmt.where(PayoutCode.status == status)
    if q:
        stmt = stmt.where(PayoutCode.pin.like(f"%{q.strip()}%"))
    codes = (await session.scalars(stmt.limit(500))).all()
    return templates.TemplateResponse(
        request,
        "payouts_list.html",
        {
            "admin_user": admin,
            "codes": [(c, payouts.effective_status(c, now)) for c in codes],
            "statuses": payout_scenarios.STATUS_OPTIONS,
            "scenarios": payout_scenarios.SCENARIOS,
            "q": q,
            "status": status,
            "generated": request.session.pop(GENERATED_PINS_KEY, None),
            "error": request.query_params.get("error"),
        },
    )


def _opt_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


@router.post("/payouts/generate", include_in_schema=False)
async def payouts_generate(
    request: Request,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    try:
        amount = int(form.get("amount", ""))
        currency = int(form.get("currency", ""))
        count = int(form.get("count") or 1)
        ttl_hours = int(form.get("ttl_hours") or 24)
        exchange_id = _opt_int(form.get("exchange_id"))
        rate = Decimal(str(form.get("rate") or "1").replace(",", "."))
        commission = Decimal(str(form.get("commission") or "0").replace(",", "."))
    except (TypeError, ValueError, InvalidOperation):
        return RedirectResponse("/admin/payouts?error=Некорректные+числовые+поля", status_code=303)
    scenario = str(form.get("scenario") or payout_scenarios.DEFAULT_SCENARIO)
    if amount <= 0 or not 1 <= count <= 100 or not 1 <= ttl_hours <= 24 * 365 or rate <= 0 or commission < 0:
        return RedirectResponse("/admin/payouts?error=Значения+вне+допустимых+границ", status_code=303)
    if scenario not in payout_scenarios.SCENARIOS:
        return RedirectResponse("/admin/payouts?error=Неизвестный+сценарий", status_code=303)

    codes = await payouts.create_codes(
        session,
        count=count,
        amount=amount,
        currency=currency,
        ttl_hours=ttl_hours,
        scenario=scenario,
        otp_needed=form.get("otp_needed") == "on",
        phone=str(form.get("phone") or "").strip()[:32] or None,
        fio=str(form.get("fio") or "").strip()[:255] or None,
        exchange_id=exchange_id,
        rate=rate,
        commission=commission,
    )
    request.session[GENERATED_PINS_KEY] = {
        "pins": [c.pin for c in codes],
        "amount": amount,
        "currency": currency,
    }
    return RedirectResponse("/admin/payouts", status_code=status.HTTP_303_SEE_OTHER)


async def _load_code(session: AsyncSession, code_id: int) -> PayoutCode:
    code = await session.scalar(
        select(PayoutCode).where(PayoutCode.id == code_id).options(selectinload(PayoutCode.events))
    )
    if code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payout code not found")
    return code


@router.get("/payouts/{code_id}", response_class=HTMLResponse)
async def payout_detail(
    request: Request,
    code_id: int,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    code = await _load_code(session, code_id)
    return templates.TemplateResponse(
        request,
        "payout_detail.html",
        {
            "admin_user": admin,
            "code": code,
            "effective_status": payouts.effective_status(code),
            "scenarios": payout_scenarios.SCENARIOS,
            "logs": await api_logs.list_for_requisite(session, code.pin),
            "otp_valid": payout_scenarios.OTP_VALID,
        },
    )


@router.post("/payouts/{code_id}/{action}", include_in_schema=False)
async def payout_action(
    request: Request,
    code_id: int,
    action: str,
    admin: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    code = await _load_code(session, code_id)
    if action == "unblock":
        await payouts.admin_unblock(session, code)
    elif action == "expire":
        await payouts.admin_expire(session, code)
    elif action == "update":
        form = await request.form()
        scenario = str(form.get("scenario") or "")
        if scenario not in payout_scenarios.SCENARIOS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown scenario")
        await payouts.admin_update(session, code, scenario=scenario, otp_needed=form.get("otp_needed") == "on")
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown action")
    return RedirectResponse(f"/admin/payouts/{code_id}", status_code=status.HTTP_303_SEE_OTHER)


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
