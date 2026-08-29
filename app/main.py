"""Точка входа: PSP-эмулятор (FastAPI).

Фаза 1 — фундамент: при старте создаются таблицы и заполняются сценарии/админ.
Фаза 2 — агентское API (/check, /pay, /status) под HTTP Basic Auth.
Фоновая задача и админка добавляются в следующих фазах.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db_init import init_db
from app.routes_api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Payment Emulator", version="0.2.0", lifespan=lifespan)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
