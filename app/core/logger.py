"""Console logging by default; file output only to an explicit directory."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging():
    logger = logging.getLogger("autoxak")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        if os.getenv("LOG_DIR"):
            directory = Path(os.environ["LOG_DIR"])
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(directory / "autoxak.log", maxBytes=10*1024*1024,
                                          backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    return logger


logger = setup_logging()
