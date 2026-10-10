import logging
import sys
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "/app/logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "autoxak.log")

def setup_logging():
    logger = logging.getLogger("autoxak")
    logger.setLevel(logging.DEBUG)

    # Единый формат с указанием модуля, функции и строки
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | [%(name)s] %(filename)s:%(lineno)d (%(funcName)s) -> %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Вывод в stdout (для docker compose logs -f)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)

    # 2. Ротация файла (хранит до 3 файлов по 10 МБ для анализа аномалий)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(stream_handler)
        logger.addHandler(file_handler)

    # Перехват логов Uvicorn в общий поток
    logging.getLogger("uvicorn.error").handlers = logger.handlers
    logging.getLogger("uvicorn.access").handlers = logger.handlers

    return logger

logger = setup_logging()