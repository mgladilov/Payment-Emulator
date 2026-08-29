"""Точка входа: PSP-эмулятор (FastAPI).

Фаза 1 — фундамент: при старте создаются таблицы и заполняются сценарии/админ.
Фаза 2 — агентское API (/check, /pay, /status) под HTTP Basic Auth.
Фаза 3 — фоновая задача автоперехода pending → финал по задержкам из БД.
Админка добавляется в следующей фазе.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.background import status_transition_loop
from app.db_init import init_db
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


app = FastAPI(title="Payment Emulator", version="0.3.0", lifespan=lifespan)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
