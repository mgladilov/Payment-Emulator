"""Точка входа: PSP-эмулятор (FastAPI).

Фаза 1 — фундамент: при старте создаются таблицы и заполняются сценарии/админ.
API-эндпоинты и админка добавляются в следующих фазах.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db_init import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Payment Emulator", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
