"""Точка входа: PSP-эмулятор (FastAPI).

Фаза 1 — фундамент: при старте создаются таблицы и заполняются сценарии/админ.
Фаза 2 — агентское API (/check, /pay, /status) под HTTP Basic Auth.
Фаза 3 — фоновая задача автоперехода pending → финал по задержкам из БД.
Фаза 4 — веб-админка (Jinja2 + HTMX) под сессионной авторизацией.
"""
import asyncio
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
from app.routes_admin import router as admin_router
from app.routes_api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop_event = asyncio.Event()
    task = asyncio.create_task(status_transition_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await task


app = FastAPI(title="Payment Emulator", version="0.4.0", lifespan=lifespan)

# Сессионная кука для админки (агентское API её не использует — там Basic Auth).
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(api_router)
app.include_router(admin_router)


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
