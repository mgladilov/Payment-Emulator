"""Логирование эмулятора.

Общий лог пишется в папку logs/ отдельным файлом на каждый день:
`logs/emulator-YYYY-MM-DD.log`. DailyFileHandler сам переключается на новый файл
при смене даты, поэтому даже долго работающий процесс после полуночи начнёт
писать в новый файл — перезапуск не нужен.
"""
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOGGER_NAME = "payment_emulator"
# Время везде в UTC — чтобы файловый лог сходился с метками в БД (тоже UTC).
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S UTC"


class DailyFileHandler(logging.FileHandler):
    """Файловый хендлер, который каждый день пишет в новый файл с датой в имени."""

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = self._today()
        super().__init__(self._path_for(self._current_date), encoding=encoding, delay=False)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _path_for(self, date_str: str) -> str:
        return str(self._log_dir / f"emulator-{date_str}.log")

    def emit(self, record: logging.LogRecord) -> None:
        today = self._today()
        if today != self._current_date:
            # Наступил новый день — переключаемся на новый файл.
            self._current_date = today
            self.baseFilename = os.path.abspath(self._path_for(today))
            if self.stream:
                self.stream.close()
                self.stream = None
            self.stream = self._open()
        super().emit(record)


def setup_logging() -> logging.Logger:
    """Настроить корневой логер эмулятора. Идемпотентно."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:  # уже настроен
        return logger

    formatter = logging.Formatter(_FORMAT, _DATEFMT)
    formatter.converter = time.gmtime  # asctime в UTC

    file_handler = DailyFileHandler(LOG_DIR)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Дочерний логер под общим `payment_emulator`."""
    base = logging.getLogger(LOGGER_NAME)
    return base.getChild(name) if name else base
