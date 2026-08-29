"""Jinja2Templates с общими фильтрами для админки."""
from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _money(minor_units: int) -> str:
    """Минимальные единицы (копейки/центы) → строка вида 123.45."""
    try:
        return f"{minor_units / 100:.2f}"
    except (TypeError, ValueError):
        return str(minor_units)


def _dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


templates.env.filters["money"] = _money
templates.env.filters["dt"] = _dt
