"""Настройки приложения. Значения читаются из окружения или .env, но имеют
разумные дефолты для локального запуска (это тестовый инструмент)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # База данных
    database_url: str = "sqlite+aiosqlite:///./emulator.db"

    # HTTP Basic Auth для агентского API (/check, /pay, /status)
    api_username: str = "agent"
    api_password: str = "agent-secret"

    # Seed-админ для веб-админки (сессионная авторизация)
    admin_username: str = "admin"
    admin_password: str = "admin"

    # Ключ подписи сессионной куки
    session_secret: str = "dev-only-change-me"


settings = Settings()
