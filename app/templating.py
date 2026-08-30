"""Jinja2Templates с общими фильтрами для админки."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _money(minor_units: int) -> str:
    """Минимальные единицы (копейки/центы) → строка вида 123.45."""
    try:
        return f"{minor_units / 100:.2f}"
    except (TypeError, ValueError):
        return str(minor_units)


def _dt(value: datetime | None) -> Markup:
    """Рендерит момент времени как <time> с ISO-меткой в UTC.

    Текст внутри — запасной вариант в UTC (виден без JS); скрипт в base.html
    подменяет его на локальную зону браузера. SQLite отдаёт наивное время —
    трактуем его как UTC.
    """
    if value is None:
        return Markup("—")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    iso = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback = escape(value.strftime("%Y-%m-%d %H:%M:%S UTC"))
    return Markup(f'<time datetime="{iso}" data-utc>{fallback}</time>')


templates.env.filters["money"] = _money
templates.env.filters["dt"] = _dt
