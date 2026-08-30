"""Точка входа: PSP-эмулятор (FastAPI).

Фаза 1 — фундамент: при старте создаются таблицы и заполняются сценарии/админ.
Фаза 2 — агентское API (/check, /pay, /status) под HTTP Basic Auth.
Фаза 3 — фоновая задача автоперехода pending → финал по задержкам из БД.
Фаза 4 — веб-админка (Jinja2 + HTMX) под сессионной авторизацией.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.admin_auth import NotAuthenticated
from app.background import status_transition_loop
from app.config import settings
from app.db_init import init_db
from app.logging_config import get_logger, setup_logging
from app.routes_admin import router as admin_router
from app.routes_api import router as api_router

setup_logging()
_request_logger = get_logger("request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _request_logger.parent.info("Payment Emulator запущен")
    stop_event = asyncio.Event()
    task = asyncio.create_task(status_transition_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await task
        _request_logger.parent.info("Payment Emulator остановлен")


app = FastAPI(title="Payment Emulator", version="0.5.0", lifespan=lifespan)

# Сессионная кука для админки (агентское API её не использует — там Basic Auth).
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Общий лог всех HTTP-запросов (пишется в файл logs/emulator-<дата>.log)."""
    if request.url.path.startswith("/static"):
        return await call_next(request)
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    client = request.client.host if request.client else "-"
    _request_logger.info(
        "%s %s -> %s (%.1f ms) client=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        client,
    )
    return response

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(api_router)
app.include_router(admin_router)


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
