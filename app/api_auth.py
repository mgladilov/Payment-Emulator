"""HTTP Basic Auth для агентского API (/check, /pay, /status).

Один сервисный логин/пароль из конфига. Это отдельный механизм от сессионной
авторизации админки — не путать.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

_basic = HTTPBasic()


def require_api_auth(credentials: HTTPBasicCredentials = Depends(_basic)) -> str:
    user_ok = secrets.compare_digest(credentials.username, settings.api_username)
    pass_ok = secrets.compare_digest(credentials.password, settings.api_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
