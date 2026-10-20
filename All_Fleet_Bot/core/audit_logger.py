"""
Unified Quant Fleet — Central Audit Logger
Provides structured, thread-safe, colored console and file logging.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

_logger = None

def setup_logger(log_dir: str = "logs", log_level: str = "INFO", log_filename: str = "fleet.log") -> logging.Logger:
    global _logger

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / log_filename

    level = getattr(logging, log_level.upper(), logging.INFO)

    logger = logging.getLogger(f"UnifiedFleet_{log_filename}")
    logger.setLevel(level)

    # Avoid duplicate handlers if already configured
    if logger.handlers:
        logger.handlers.clear()

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Ensure console output handles emojis and UTF-8 cleanly
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler (10MB max, keep 5 backups)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Do not propagate to root logger to avoid duplicate console lines
    logger.propagate = False

    logger.info(f"📑 RotatingFileHandler initialized: {log_file} (maxBytes=10MB, backupCount=5, UTF-8)")

    _logger = logger
    return _logger

def get_logger(log_filename: str = "fleet.log") -> logging.Logger:
    global _logger
    if _logger is None:
        return setup_logger(log_filename=log_filename)
    return _logger
