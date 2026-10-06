"""Настройки приложения. Значения читаются из окружения или .env, но имеют
разумные дефолты для локального запуска (это тестовый инструмент)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # База данных (PostgreSQL через asyncpg). Дефолт совпадает с docker-compose.yml.
    database_url: str = "postgresql+asyncpg://payment_emulator:payment_emulator@localhost:5432/payment_emulator"
    # NullPool — без пула соединений. Нужен тестам: каждый тест живёт в своём
    # event loop, а соединения asyncpg привязаны к циклу, в котором созданы.
    database_null_pool: bool = False
    # Применять миграции Alembic при старте приложения (alembic upgrade head).
    auto_migrate: bool = True

    # Первая агентская учётка (HTTP Basic Auth для /check, /pay, /status, /api/*).
    # Создаётся, только если агентов в БД нет; дальше — управление в админке.
    api_username: str = "agent"
    api_password: str = "agent-secret"

    # Первый админ веб-админки. Создаётся, только если админов в БД нет;
    # пароль затем меняется в админке, эти значения больше не читаются.
    admin_username: str = "admin"
    admin_password: str = "admin"

    # Стоимость bcrypt для паролей (4..31). Тесты ставят 4 ради скорости.
    bcrypt_rounds: int = 12

    # Ключ подписи сессионной куки
    session_secret: str = "dev-only-change-me"


settings = Settings()
